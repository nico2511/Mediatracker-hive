#!/usr/bin/env python3
"""Recette Hive T8 derrière un proxy nginx local (non destructif par défaut)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

from scripts.hive_t8_lib import (  # noqa: E402
    HOSTNAMES,
    NOW,
    PORTS,
    REACHABILITY_TTL_DOC,
    RUNTIME_DIR,
    SANDBOX_IP,
    URL_X,
    URL_Y,
    URL_Z,
    ApiSession,
    InstanceProc,
    NginxProc,
    assert_no_leak,
    cleanup_tracked_processes,
    ensure_hosts_file,
    ensure_hosts_hint,
    ensure_sandbox_ip,
    generate_tls_material,
    join_pair,
    portal_blocks_non_hive,
    pull_gossip,
    revert_hosts_delta,
    save_report,
    seed_file_production,
    download_peer_file,
    transfer_via_proxy,
    write_nginx_config,
)


def _print_plan() -> None:
    print("=== Hive T8 — plan d'exécution ===")
    print(f"IP sandbox : {SANDBOX_IP} (TEST-NET documentation, évite blocage SSRF sur 127.0.0.1)")
    print(ensure_hosts_hint())
    print("Domaines :", ", ".join(HOSTNAMES))
    print("Backends :", PORTS)
    print("Proxy : nginx TLS, /hive/ sans buffering, / → 401")
    print("Scénarios : appairage, gossip bidirectionnel, Range+reprise, exclusion, 2 domaines, repartage A→B")
    print("Par défaut : aucun processus démarré. Relancer avec --execute.")
    print("Sécurité : PIDs suivis dans runtime/pids.json ; delta /etc/hosts dans runtime/hosts.delta")


def _scenario_matrix() -> list[dict]:
    return [
        {
            "id": "proxy-portail",
            "exigence": "/ hors /hive/ protégé (portail simulé 401)",
            "cmd": "GET https://mtr.exemple.fr/api/v1/health",
        },
        {
            "id": "appairage",
            "exigence": "Appairage via join HTTP signé derrière proxy",
            "cmd": "join_pair creator(Y) invitee(X)",
        },
        {
            "id": "gossip",
            "exigence": "Catalogue synchronisé via gossip/sync (Y↔X derrière proxy)",
            "cmd": "hive_gossip_tick force → Y et X",
        },
        {
            "id": "range-reprise",
            "exigence": "Range transmis ; reprise transfert ; empreintes intermédiaires",
            "cmd": "GET Range pair partiel puis complet + finalize_received_hive_file",
        },
        {
            "id": "exclusion",
            "exigence": "Exclu → 401 /hive/ et tombstones ; autre ruche OK",
            "cmd": "signed_peer_request access-check + etat retire",
        },
        {
            "id": "deux-domaines",
            "exigence": "Même IP, 2 membres, limites gossip indépendantes",
            "cmd": "PeerRateLimiter limit=2 sur Y",
        },
        {
            "id": "repartage",
            "exigence": "Y(A)→X(A+B)→Z(B) ; catalogue indisponible si X arrêté",
            "cmd": "réception HTTP pair, finalize_received_hive_file, GET /hive/catalogue",
        },
    ]


def _result(
    scenario_id: str,
    *,
    passed: bool,
    assertions: dict,
    limite: str = "",
) -> dict:
    return {
        "id": scenario_id,
        "statut": "PASS" if passed else "FAIL",
        "assertions": assertions,
        "preuve": "; ".join(f"{k}={v}" for k, v in assertions.items() if k != "detail"),
        "limite": limite,
    }


def _teardown(instances: list[InstanceProc], nginx: NginxProc | None, *, keep: bool) -> None:
    for inst in instances:
        inst.stop()
    if nginx:
        nginx.stop()
    cleanup_tracked_processes()
    if not keep:
        revert_hosts_delta()
    if not keep and RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR, ignore_errors=True)


def run_execute(*, keep: bool) -> int:
    from sqlalchemy import select

    from mediatracker.api.app import create_app
    from mediatracker.config import Settings
    from mediatracker.db.models import HiveEntry, HiveMember
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
    from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
    from mediatracker.hive.peer_auth import sign_request_headers
    from mediatracker.hive.peer_client import signed_peer_request
    from mediatracker.hive.peer_gossip import origin_is_reachable
    from mediatracker.hive.peer_rate_limit import PeerRateLimiter
    results: list[dict] = []
    setup_notes = ensure_sandbox_ip() + ensure_hosts_file()
    ca = generate_tls_material()
    cfg = write_nginx_config()
    import os

    os.environ["SSL_CERT_FILE"] = str(ca)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    instances = [
        InstanceProc("y", PORTS["y"], URL_Y, RUNTIME_DIR / "inst-y"),
        InstanceProc("x", PORTS["x"], URL_X, RUNTIME_DIR / "inst-x"),
        InstanceProc("z", PORTS["z"], URL_Z, RUNTIME_DIR / "inst-z"),
    ]
    nginx = NginxProc(cfg)
    try:
        for inst in instances:
            inst.start()
        nginx.start()

        api_y = ApiSession(instances[0].local_api_base())
        api_x = ApiSession(instances[1].local_api_base())
        api_z = ApiSession(instances[2].local_api_base())

        api_y.enable_hive("yy", URL_Y)
        api_x.enable_hive("xx", URL_X)
        api_z.enable_hive("zz", URL_Z)

        ok_portal, detail = portal_blocks_non_hive(URL_Y)
        results.append(
            _result("proxy-portail", passed=ok_portal, assertions={"detail": detail})
        )

        rid_a = join_pair(
            creator=api_y,
            invitee=api_x,
            creator_public_url=URL_Y,
            invitee_public_url=URL_X,
            invitee_name="xx",
        )
        results.append(
            _result("appairage", passed=True, assertions={"ruche_a": rid_a})
        )

        def _app_for(inst: InstanceProc):
            s = Settings(
                secret_key=inst.env()["SECRET_KEY"],
                admin_username="admin",
                admin_password=inst.env()["ADMIN_PASSWORD"],
                database_url=inst.env()["DATABASE_URL"],
                data_dir=Path(inst.env()["DATA_DIR"]),
                config_dir=Path(inst.env()["CONFIG_DIR"]),
                disk_min_free_gb=0,
            )
            return create_app(s)

        app_y = _app_for(instances[0])
        app_x = _app_for(instances[1])
        app_z = _app_for(instances[2])
        for app in (app_y, app_x, app_z):
            app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)

        ruche_a = bytes.fromhex(rid_a)
        payload = b"HIVE-T8-" + b"R" * 12000
        origin_y, fid_y, sha = seed_file_production(app_y, ruche_id=ruche_a, payload=payload, title="DemoY")

        # Ruche B (repartage) avant finalize sur X
        rid_bx = api_x.create_ruche()
        join_pair(
            creator=api_x,
            invitee=api_z,
            creator_public_url=URL_X,
            invitee_public_url=URL_Z,
            invitee_name="zz",
            ruche_id_hex=rid_bx,
        )
        ruche_bx = bytes.fromhex(rid_bx)

        # Gossip bidirectionnel Y ↔ X via proxy
        db_yg = app_y.state.session_factory()
        db_xg = app_x.state.session_factory()
        try:
            pull_gossip(db_xg, app_x.state.settings, ruche_id=ruche_a, peer_url=URL_Y)
            db_xg.commit()
            pull_gossip(db_yg, app_y.state.settings, ruche_id=ruche_a, peer_url=URL_X)
            db_yg.commit()
            row_x = db_xg.scalar(
                select(HiveEntry).where(HiveEntry.ruche_id == ruche_a, HiveEntry.sha256_hex == sha).limit(1)
            )
            row_y = db_yg.scalar(
                select(HiveEntry)
                .where(HiveEntry.ruche_id == ruche_a, HiveEntry.origin_instance_id == origin_y)
                .limit(1)
            )
            gossip_ok = row_x is not None and row_y is not None
            gossip_taille = None
            if row_x is not None and row_x.payload_json:
                try:
                    gossip_doc = json.loads(row_x.payload_json)
                    gossip_taille = gossip_doc.get("taille")
                except json.JSONDecodeError:
                    gossip_taille = None
            taille_ok = gossip_taille == len(payload)
            gossip_pass = gossip_ok and taille_ok
            results.append(
                _result(
                    "gossip",
                    passed=gossip_pass,
                    assertions={
                        "x_via_mtr_exemple": row_x is not None,
                        "y_via_mtr_test": row_y is not None,
                        "bidirectional": gossip_ok,
                        "gossip_taille_bytes": gossip_taille,
                        "expected_taille": len(payload),
                    },
                    limite="tour gossip forcé, pas attente 10 min",
                )
            )
        finally:
            db_yg.close()
            db_xg.close()

        sources_a = [
            {"ruche_id_hex": rid_a, "origine": origin_y, "file_id_hex": fid_y, "sha256": sha},
        ]
        db_x = app_x.state.session_factory()
        range_assert: dict = {}
        try:
            import httpx

            pk_x = load_private_key(app_x.state.settings)
            path = f"/hive/files/{fid_y}"
            headers = sign_request_headers(
                private_key=pk_x,
                method="GET",
                path=path,
                query="",
                ruche_id=ruche_a,
                body=b"",
            )
            headers["Range"] = "bytes=0-99"
            with httpx.Client(verify=str(ca), timeout=30.0) as hc:
                r206 = hc.get(URL_Y + path, headers=headers)
            range_assert["range_status"] = r206.status_code
            range_assert["range_bytes"] = len(r206.content)

            half = len(payload) // 2
            fiche = {"type": "movie", "tmdb_id": 99902, "title": "SandboxRelay", "year": 2099}
            cle = {"type": "movie", "tmdb_id": 99902, "version": "u+u"}
            part = Path(app_x.state.settings.data_dir) / "sandbox" / "t8-range.part"
            part.unlink(missing_ok=True)
            job2 = download_peer_file(
                db_x,
                app_x.state.settings,
                sources=sources_a,
                sha256_hex=sha,
                expected_size=len(payload),
                part_path=part,
                fiche=fiche,
                cle=cle,
                max_bytes=half,
                finalize=False,
            )
            db_x.commit()
            bytes_at_half = job2.bytes_done
            part_half_sha = file_sha256_hex(part) if part.is_file() else None
            range_assert["bytes_done_half"] = bytes_at_half
            range_assert["expected_half"] = half
            range_assert["reached_half"] = bytes_at_half >= half
            range_assert["part_sha_at_half"] = part_half_sha

            job2 = download_peer_file(
                db_x,
                app_x.state.settings,
                sources=sources_a,
                sha256_hex=sha,
                expected_size=len(payload),
                part_path=part,
                fiche=fiche,
                cle=cle,
                finalize=True,
            )
            db_x.commit()

            part_full_sha = file_sha256_hex(part) if part.is_file() else None
            range_assert["job_state"] = job2.state
            range_assert["part_sha_final"] = part_full_sha
            range_assert["expected_sha"] = sha

            origin_x = hive_public_id_from_public_key(load_private_key(app_x.state.settings).public_key())
            row_b_auto = db_x.scalar(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == ruche_bx,
                    HiveEntry.origin_instance_id == origin_x,
                    HiveEntry.etat == "disponible",
                ).limit(1)
            )
            range_assert["auto_hive_entry_b"] = row_b_auto is not None

            range_ok = (
                r206.status_code == 206
                and len(r206.content) == 100
                and bytes_at_half >= half
                and job2.bytes_done == len(payload)
                and part_half_sha
                and job2.sha256_hex == sha.lower()
                and job2.state == "done"
                and row_b_auto is not None
            )
        finally:
            db_x.close()

        results.append(
            _result(
                "range-reprise",
                passed=range_ok,
                assertions=range_assert,
            )
        )

        # Même IP / limites indépendantes
        instances[0].stop()
        instances[0].peer_rate_limit = "2"
        instances[0].start()
        time.sleep(0.4)
        app_y = _app_for(instances[0])
        app_y.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        pk_y = load_private_key(app_y.state.settings)
        pk_x = load_private_key(app_x.state.settings)
        assert pk_y and pk_x
        rid_rate = api_y.create_ruche()
        join_pair(
            creator=api_y,
            invitee=api_z,
            creator_public_url=URL_Y,
            invitee_public_url=URL_Z,
            invitee_name="zz",
            ruche_id_hex=rid_rate,
        )
        rid_bytes = bytes.fromhex(rid_rate)
        codes_y = [
            signed_peer_request(
                base_url=URL_Y,
                method="GET",
                path=f"/hive/ruche/{rid_rate}/access-check",
                ruche_id=rid_bytes,
                private_key=pk_y,
            ).status_code
            for _ in range(3)
        ]
        pk_z = load_private_key(app_z.state.settings)
        assert pk_z
        code_z = signed_peer_request(
            base_url=URL_Y,
            method="GET",
            path=f"/hive/ruche/{rid_rate}/access-check",
            ruche_id=rid_bytes,
            private_key=pk_z,
        ).status_code
        rate_ok = codes_y == [204, 204, 429] and code_z == 204
        results.append(
            _result(
                "deux-domaines",
                passed=rate_ok,
                assertions={"y_burst": codes_y, "z_to_y": code_z, "sandbox_ip": SANDBOX_IP},
                limite="débit par clé Hive-Id",
            )
        )

        # Repartage : catalogue B via gossip depuis X, transfert Z via proxy
        y_id = api_y.identity()["instance_id"]
        origin_x = hive_public_id_from_public_key(pk_x.public_key())
        db_x4 = app_x.state.session_factory()
        try:
            row_b = db_x4.scalar(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == ruche_bx,
                    HiveEntry.origin_instance_id == origin_x,
                    HiveEntry.etat == "disponible",
                ).limit(1)
            )
            assert row_b and row_b.file_id
            fid_b = row_b.file_id.hex()
            sha_b = row_b.sha256_hex or sha
        finally:
            db_x4.close()

        rep: dict = {}
        db_z = app_z.state.session_factory()
        try:
            pull_gossip(db_z, app_z.state.settings, ruche_id=ruche_bx, peer_url=URL_X)
            db_z.commit()
            rows = list(db_z.scalars(select(HiveEntry).where(HiveEntry.ruche_id == ruche_bx)).all())
            forbidden = {y_id, rid_a, origin_y}
            leaks: list[str] = []
            for r in rows:
                leaks.extend(assert_no_leak(r.payload_json, forbidden=forbidden))
            rep["leak_tokens"] = sorted(set(leaks))
            rep["leak_y_origin"] = y_id in {r.origin_instance_id for r in rows}
            members_b = {
                m.member_instance_id
                for m in db_z.scalars(select(HiveMember).where(HiveMember.ruche_id == ruche_bx)).all()
            }
            rep["y_in_ruche_b"] = y_id in members_b

            src_b = [
                {"ruche_id_hex": rid_bx, "origine": origin_x, "file_id_hex": fid_b, "sha256": sha_b},
            ]
            job_z = transfer_via_proxy(
                db_z, app_z.state.settings, sha=sha_b, size=len(payload), sources=src_b, finalize=False
            )
            rep["z_job_state"] = job_z.state
            rep["z_sha_match"] = job_z.sha256_hex == sha.lower() and job_z.state == "verified"

            refresh_hive_catalogue_store(db_z, now=NOW)
            db_z.commit()
            cat_before = api_z.get_catalogue(refresh=False)
            rep["catalogue_versions_before_stop"] = len(cat_before.get("items") or [])

            instances[1].stop()
            time.sleep(0.3)

            probe_part = Path(app_z.state.settings.data_dir) / "sandbox" / "t8-probe.part"
            probe_part.unlink(missing_ok=True)
            job_probe = download_peer_file(
                db_z,
                app_z.state.settings,
                sources=src_b,
                sha256_hex=sha_b,
                expected_size=len(payload),
                part_path=probe_part,
                max_bytes=1024,
                finalize=False,
            )
            db_z.commit()
            rep["probe_paused"] = job_probe.paused_reason
            rep["origin_unreachable"] = not origin_is_reachable(
                db_z, ruche_id=ruche_bx, origin_id=origin_x, now=NOW
            )

            refresh_hive_catalogue_store(db_z, now=NOW)
            db_z.commit()
            cat_after = api_z.get_catalogue(refresh=True)
            versions = []
            for work in cat_after.get("items") or []:
                for ver in work.get("versions") or []:
                    versions.append(ver)
            rep["catalogue_disponible_flags"] = [bool(v.get("disponible")) for v in versions]
            indispo_api = any(v.get("disponible") is False for v in versions) if versions else False
            rep["indisponible_via_api"] = indispo_api
            rep["reachability_ttl_doc"] = REACHABILITY_TTL_DOC

            st_x_down = signed_peer_request(
                base_url=URL_X,
                method="GET",
                path=f"/hive/ruche/{rid_bx}/access-check",
                ruche_id=ruche_bx,
                private_key=pk_z,
            ).status_code
            rep["access_check_x_stopped"] = st_x_down
        finally:
            db_z.close()

        repartage_ok = (
            rep.get("z_sha_match")
            and not rep.get("leak_tokens")
            and not rep.get("leak_y_origin")
            and not rep.get("y_in_ruche_b")
            and rep.get("indisponible_via_api")
            and rep.get("origin_unreachable")
        )
        results.append(
            _result(
                "repartage",
                passed=bool(repartage_ok),
                assertions=rep,
                limite="indisponibilité via mark_origin_unreachable après échec GET pair (TTL 3600s documenté)",
            )
        )

        # Exclusion + tombstones sur ruche exclue
        instances[0].stop()
        instances[0].peer_rate_limit = None
        instances[0].start()
        instances[1].start()
        time.sleep(0.5)
        app_y = _app_for(instances[0])
        app_x = _app_for(instances[1])
        app_y.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        app_x.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)

        rid_ex = api_y.create_ruche()
        join_pair(
            creator=api_y,
            invitee=api_x,
            creator_public_url=URL_Y,
            invitee_public_url=URL_X,
            invitee_name="xx",
            ruche_id_hex=rid_ex,
        )
        rid_sis = api_y.create_ruche()
        join_pair(
            creator=api_y,
            invitee=api_x,
            creator_public_url=URL_Y,
            invitee_public_url=URL_X,
            invitee_name="xx",
            ruche_id_hex=rid_sis,
        )
        x_id = api_x.identity()["instance_id"]
        db_ex = app_y.state.session_factory()
        try:
            for rid_hex in (rid_ex, rid_sis):
                db_ex.add(
                    HiveEntry(
                        ruche_id=bytes.fromhex(rid_hex),
                        origin_instance_id=x_id,
                        seq=1,
                        etat="disponible",
                        sha256_hex="a" * 64,
                        payload_json='{"demo":true}',
                        signature_b64="e30=",
                    )
                )
            db_ex.commit()
        finally:
            db_ex.close()

        api_y.exclude(rid_ex, x_id)
        pk_x = load_private_key(app_x.state.settings)
        st_ex = signed_peer_request(
            base_url=URL_Y,
            method="GET",
            path=f"/hive/ruche/{rid_ex}/access-check",
            ruche_id=bytes.fromhex(rid_ex),
            private_key=pk_x,
        ).status_code
        st_sister = signed_peer_request(
            base_url=URL_Y,
            method="GET",
            path=f"/hive/ruche/{rid_sis}/access-check",
            ruche_id=bytes.fromhex(rid_sis),
            private_key=pk_x,
        ).status_code
        db_chk = app_y.state.session_factory()
        try:
            rows_ex = list(
                db_chk.scalars(
                    select(HiveEntry).where(
                        HiveEntry.ruche_id == bytes.fromhex(rid_ex),
                        HiveEntry.origin_instance_id == x_id,
                    )
                ).all()
            )
            rows_sis = list(
                db_chk.scalars(
                    select(HiveEntry).where(
                        HiveEntry.ruche_id == bytes.fromhex(rid_sis),
                        HiveEntry.origin_instance_id == x_id,
                    )
                ).all()
            )
            tomb_ex = all(r.etat == "retire" for r in rows_ex)
            sis_ok = all(r.etat == "disponible" for r in rows_sis)
        finally:
            db_chk.close()

        excl_ok = st_ex == 401 and st_sister == 204 and tomb_ex and sis_ok
        results.append(
            _result(
                "exclusion",
                passed=excl_ok,
                assertions={
                    "access_exclu": st_ex,
                    "access_soeur": st_sister,
                    "tombstone_exclu": tomb_ex,
                    "soeur_disponible": sis_ok,
                },
            )
        )

        for app in (app_y, app_x, app_z):
            app.state.engine.dispose()
        api_y.close()
        api_x.close()
        api_z.close()

        fail = [r for r in results if r.get("statut") != "PASS"]
        report = {
            "generated_at": NOW.isoformat(),
            "execute": True,
            "sandbox_ip": SANDBOX_IP,
            "public_urls": {"y": URL_Y, "x": URL_X, "z": URL_Z},
            "setup_notes": setup_notes,
            "results": results,
            "summary": {"pass": len(results) - len(fail), "fail": len(fail)},
        }
        save_report(RUNTIME_DIR / "report.json", report)
        print("Notes setup:", *setup_notes, sep="\n  ")
        for row in results:
            print(f"[{row['statut']}] {row['id']}: {row.get('preuve', '')}")
        return 1 if fail else 0
    finally:
        _teardown(instances, nginx, keep=keep)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recette Hive T8 (proxy local)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Lance nginx, uvicorn et scénarios (PIDs dans runtime/pids.json)",
    )
    parser.add_argument("--keep", action="store_true", help="Conserve sandbox/hive-t8/runtime/")
    args = parser.parse_args()
    if not args.execute:
        _print_plan()
        print("\nMatrice (prévue) :")
        for row in _scenario_matrix():
            print(f"  - {row['id']}: {row['exigence']}")
        return 0
    return run_execute(keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
