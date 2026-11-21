<script setup lang="ts">
import { computed, ref } from "vue";

import ActionMenuPopover from "./ActionMenuPopover.vue";
import FicheHideMenuEntry from "./FicheHideMenuEntry.vue";
import StatusIcon from "./StatusIcon.vue";
import TrailerPlayerDialog from "./TrailerPlayerDialog.vue";
import { trailerSubtitle } from "../trailerSubtitle";
import { fileStatusIcon, fileStatusLabel } from "../fileStatus";
import { formatParis } from "../time";

interface Poll {
  allowed: boolean;
  reason: string;
  last_check_at: string | null;
  next_check_at: string | null;
  last_result: string | null;
  last_message: string | null;
  interval_min: number;
}

interface NextEpisode {
  season: number | null;
  episode: number | null;
  title: string | null;
  air_date: string | null;
}

const props = defineProps<{
  mediaType: string;
  followed: boolean;
  onHold: boolean;
  autoDownload: boolean;
  poll: Poll;
  progressWatched: number;
  progressTotal: number;
  availableNotDownloaded: number;
  nextEpisode: NextEpisode | null;
  fileStatus: string;
  /** Film déjà obtenu puis perdu (trace fichier_absent), pas jamais téléchargé. */
  filmFileAbsent?: boolean;
  filmObtained: boolean;
  filmNotYetAvailable?: boolean;
  filmStatusLine?: string | null;
  checking: boolean;
  primaryTrashEntryId?: number | null;
  seriesWatchMenu?: boolean;
  /** all (défaut), etat seul (repli Suivi), actions seul (en-tête). */
  section?: "all" | "etat" | "actions";
  tmdbUrl?: string | null;
  jellyfinWebUrl?: string | null;
  tmdbTrailerYoutubeKey?: string | null;
  tmdbTrailerKind?: string | null;
  tmdbTrailerLanguage?: string | null;
  /** Masque les titres de section (bandeau actions série). */
  compact?: boolean;
  hidden?: boolean;
}>();

const trailerOpen = ref(false);
const showTrailerButton = computed(() => Boolean(props.tmdbTrailerYoutubeKey));
const trailerSubtitleLabel = computed(() =>
  trailerSubtitle(props.tmdbTrailerKind, props.tmdbTrailerLanguage),
);

const showEtat = computed(() => (props.section ?? "all") !== "actions");
const showActions = computed(() => (props.section ?? "all") !== "etat");

const emit = defineEmits<{
  check: [];
  pause: [paused: boolean];
  auto: [enabled: boolean];
  restore: [];
  redownload: [];
  unfollow: [];
  hide: [];
  followAgain: [];
  watch: [body: Record<string, unknown>];
}>();

const results: Record<string, string> = {
  found: "trouvé",
  nothing: "rien",
  error: "erreur",
};

const reasons: Record<string, string> = {
  before_air_date: "Les sources seront interrogées à partir de la date de disponibilité numérique.",
  film_missing: "Recherche automatique tant que le film est suivi, absent et non vu.",
  air_window: "Recherche sur les sources autorisée, dans la limite de l'intervalle.",
  no_air_date: "Pas de date TMDB : le passage suit l'intervalle configuré.",
  outside_air_window: "Hors de la fenêtre qui suit la diffusion.",
  interval: "L'intervalle depuis le dernier passage n'est pas écoulé.",
  backoff: "Le prochain passage est déjà planifié.",
  on_hold: "Suivi en pause.",
  catch_up: "Rattrapage des épisodes manquants.",
  missing_episode: "Épisode non vu sans fichier : le passage comble le trou, même hors de la fenêtre de diffusion.",
  series_finished: "Série terminée : pas de passage.",
  policy_off: "Politique des nouveaux épisodes : off.",
  film_obtained: "Film obtenu : plus de vérification nécessaire.",
};

function slot(season: number | null, episode: number | null): string {
  if (season == null || episode == null) {
    return "";
  }
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const [year, month, day] = value.split("-");
  if (!day || !month || !year) {
    return value;
  }
  return `${day}/${month}/${year}`;
}

const lastCheckLabel = computed(() => {
  const formatted = formatParis(props.poll.last_check_at);
  return formatted ? `${formatted} (Paris)` : "jamais";
});

const nextCheckLabel = computed(() => {
  if (props.filmObtained) {
    return "aucune";
  }
  return formatParis(props.poll.next_check_at) || "dès que la fenêtre de diffusion l'autorise";
});

const pollFootnote = computed(() => reasons[props.poll.reason] || props.poll.reason || "");

/** Même règle que le bouton principal : « fichiers supprimés » seulement si trace file_absent. */
const fileStatusDetailed = computed(
  () => props.mediaType === "film" && Boolean(props.filmFileAbsent),
);

const primaryAction = computed(() => {
  if (props.filmNotYetAvailable) {
    return null;
  }
  if (props.primaryTrashEntryId) {
    return { key: "restore", label: "Restaurer", icon: "undo" };
  }
  if (props.fileStatus === "absent" && props.mediaType === "film") {
    if (props.filmFileAbsent) {
      return { key: "redownload", label: "Retélécharger", icon: "refresh" };
    }
    return { key: "check", label: "Rechercher maintenant", icon: "refresh" };
  }
  return { key: "check", label: "Vérifier maintenant", icon: "refresh" };
});

const showPrimary = computed(() => primaryAction.value !== null);

const checkDisabled = computed(() => props.checking || props.filmObtained);

const primaryDisabled = computed(() => {
  const action = primaryAction.value;
  if (action === null) {
    return true;
  }
  if (action.key === "check") {
    return checkDisabled.value;
  }
  return props.checking;
});

const showCheckInMore = computed(() => primaryAction.value !== null && primaryAction.value.key !== "check");

function runPrimary() {
  const action = primaryAction.value;
  if (action === null) {
    return;
  }
  const key = action.key;
  if (key === "restore") {
    emit("restore");
  } else if (key === "redownload") {
    emit("redownload");
  } else {
    emit("check");
  }
}

function checkedFrom(event: Event): boolean {
  return event.target instanceof HTMLInputElement && event.target.checked;
}
</script>

<template>
  <article class="fiche-state-actions" :class="{ panel: (section ?? 'all') === 'all' }">
    <div class="fiche-state-actions-grid" :class="{ 'fiche-state-actions-grid--single': !showEtat || !showActions }">
      <section v-if="showEtat" class="fiche-etat" aria-labelledby="fiche-etat-title">
        <h2 id="fiche-etat-title">État</h2>
        <dl class="fiche-etat-lines">
          <template v-if="mediaType === 'series'">
            <div>
              <dt>Vus</dt>
              <dd>{{ progressWatched }} sur {{ progressTotal }}</dd>
            </div>
            <div>
              <dt>Sur les sources</dt>
              <dd>
                <template v-if="availableNotDownloaded">
                  {{ availableNotDownloaded }}, pas téléchargé{{ availableNotDownloaded > 1 ? "s" : "" }}
                </template>
                <template v-else>aucun</template>
              </dd>
            </div>
            <div v-if="nextEpisode">
              <dt>Prochain</dt>
              <dd>
                {{ slot(nextEpisode.season, nextEpisode.episode) }}
                <template v-if="nextEpisode.air_date">, {{ formatDate(nextEpisode.air_date) }}</template>
              </dd>
            </div>
          </template>
          <template v-else>
            <div>
              <dt>Progression</dt>
              <dd>{{ progressWatched }} vu sur {{ progressTotal || 1 }}</dd>
            </div>
            <div v-if="filmStatusLine">
              <dt>Disponibilité</dt>
              <dd>{{ filmStatusLine }}</dd>
            </div>
          </template>
          <div v-if="!(mediaType === 'film' && filmNotYetAvailable)">
            <dt>Fichier</dt>
            <dd class="icon-line">
              <StatusIcon
                :name="fileStatusIcon(fileStatus)"
                :label="fileStatusLabel(fileStatus, fileStatusDetailed)"
              />
              <span>{{ fileStatusLabel(fileStatus, fileStatusDetailed) }}</span>
            </dd>
          </div>
          <div>
            <dt>Dernière vérification</dt>
            <dd>
              {{ lastCheckLabel }}
              <template v-if="poll.last_result">
                · {{ results[poll.last_result] || poll.last_result }}
              </template>
            </dd>
          </div>
          <div>
            <dt>Prochaine</dt>
            <dd>{{ nextCheckLabel }}</dd>
          </div>
        </dl>
        <p v-if="pollFootnote" class="hint fiche-etat-foot">{{ pollFootnote }}</p>
        <p v-if="poll.last_message" class="hint">{{ poll.last_message }}</p>
      </section>

      <section
        v-if="showActions"
        class="fiche-actions"
        :aria-labelledby="compact ? undefined : 'fiche-actions-title'"
      >
        <h2 v-if="!compact" id="fiche-actions-title">Actions</h2>
        <p v-if="!followed" class="fiche-primary-row">
          <button type="button" class="primary fiche-primary-action" @click="emit('followAgain')">Suivre à nouveau</button>
          <button
            v-if="showTrailerButton"
            type="button"
            class="fiche-trailer-btn"
            data-testid="fiche-trailer-open"
            @click="trailerOpen = true"
          >
            <StatusIcon name="play" label="Bande-annonce" />
            Bande-annonce
          </button>
        </p>
        <p v-else-if="showPrimary" class="fiche-primary-row">
          <button type="button" class="primary fiche-primary-action" :disabled="primaryDisabled" @click="runPrimary">
            <StatusIcon
              :name="primaryAction!.icon"
              :label="primaryAction!.label"
              :spin="checking && primaryAction!.key === 'check'"
            />
            {{ primaryAction!.label }}
          </button>
          <button
            v-if="showTrailerButton"
            type="button"
            class="fiche-trailer-btn"
            data-testid="fiche-trailer-open"
            @click="trailerOpen = true"
          >
            <StatusIcon name="play" label="Bande-annonce" />
            Bande-annonce
          </button>
        </p>
        <p v-else-if="filmNotYetAvailable && filmStatusLine" class="fiche-primary-row">
          <span class="hint">{{ filmStatusLine }}</span>
          <button
            v-if="showTrailerButton"
            type="button"
            class="fiche-trailer-btn"
            data-testid="fiche-trailer-open"
            @click="trailerOpen = true"
          >
            <StatusIcon name="play" label="Bande-annonce" />
            Bande-annonce
          </button>
        </p>
        <p v-else-if="showTrailerButton && !showPrimary" class="fiche-primary-row">
          <button
            type="button"
            class="fiche-trailer-btn"
            data-testid="fiche-trailer-open"
            @click="trailerOpen = true"
          >
            <StatusIcon name="play" label="Bande-annonce" />
            Bande-annonce
          </button>
        </p>
        <p class="fiche-secondary-actions">
          <span v-if="tmdbUrl || jellyfinWebUrl" class="fiche-ext-icon-links" role="group" aria-label="Liens externes">
            <a
              v-if="tmdbUrl"
              class="icon-button fiche-ext-icon-link"
              :href="tmdbUrl"
              target="_blank"
              rel="noopener noreferrer"
              title="Ouvrir la fiche TMDB"
              aria-label="Ouvrir la fiche TMDB"
            >
              <span class="tmdb-mark" aria-hidden="true">TM</span>
            </a>
            <a
              v-if="jellyfinWebUrl"
              class="icon-button fiche-ext-icon-link"
              :href="jellyfinWebUrl"
              target="_blank"
              rel="noopener noreferrer"
              title="Lire sur Jellyfin"
              aria-label="Lire sur Jellyfin"
            >
              <StatusIcon name="play" label="Lire sur Jellyfin" />
            </a>
          </span>
          <button v-if="followed && !onHold" type="button" @click="emit('pause', true)">
            <StatusIcon name="pause" label="Pause" /> Mettre en pause
          </button>
          <button v-else-if="followed && onHold" type="button" @click="emit('pause', false)">
            Reprendre
          </button>
          <ActionMenuPopover
            v-if="followed || showCheckInMore || seriesWatchMenu"
            class="more-actions"
            trigger-variant="fiche"
            menu-aria-label="Plus d'actions sur la fiche"
          >
            <button
              v-if="filmNotYetAvailable"
              type="button"
              class="action-menu-item"
              role="menuitem"
              :disabled="checking"
              @click="emit('check')"
            >
              <StatusIcon name="refresh" label="Chercher" :spin="checking" /> Chercher quand même
            </button>
            <button
              v-if="showCheckInMore"
              type="button"
              class="action-menu-item"
              role="menuitem"
              :disabled="checkDisabled"
              @click="emit('check')"
            >
              <StatusIcon name="refresh" label="Vérifier" :spin="checking" /> Vérifier maintenant
            </button>
            <template v-if="seriesWatchMenu">
              <button type="button" class="action-menu-item" role="menuitem" @click="emit('watch', { all: true })">
                Marquer la série comme vue
              </button>
              <button
                type="button"
                class="action-menu-item"
                role="menuitem"
                @click="emit('watch', { all: true, undo: true })"
              >
                Marquer la série comme non vue
              </button>
            </template>
            <template v-if="!hidden">
              <hr class="action-menu-separator" role="none" />
              <FicheHideMenuEntry @hide="emit('hide')" />
            </template>
            <button v-if="followed" type="button" class="action-menu-item danger" role="menuitem" @click="emit('unfollow')">
              Ne plus suivre…
            </button>
          </ActionMenuPopover>
        </p>
        <label v-if="followed && (section ?? 'all') === 'all'" class="switch-line">
          <span>Téléchargement automatique</span>
          <span class="switch">
            <input type="checkbox" :checked="autoDownload" @change="emit('auto', checkedFrom($event))" />
            <span class="switch-ui" aria-hidden="true"></span>
          </span>
        </label>
      </section>
    </div>
    <TrailerPlayerDialog
      :open="trailerOpen"
      :youtube-key="tmdbTrailerYoutubeKey || ''"
      :subtitle="trailerSubtitleLabel"
      @close="trailerOpen = false"
    />
  </article>
</template>
