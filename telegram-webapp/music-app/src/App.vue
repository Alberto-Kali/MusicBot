<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

type Track = {
  videoId: string
  title: string
  artist: string
  duration?: number | null
  thumbnail?: string | null
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

const query = ref('')
const loading = ref(false)
const error = ref('')
const info = ref('')
const tracks = ref<Track[]>([])
const telegramUserId = ref<number | null>(null)

const queue = ref<Track[]>([])
const baseQueue = ref<Track[]>([])
const currentIndex = ref(-1)
const audioSrc = ref('')
const isPlaying = ref(false)
const playerExpanded = ref(false)
const repeatMode = ref<'off' | 'all' | 'one'>('off')
const mixEnabled = ref(false)
const audioRef = ref<HTMLAudioElement | null>(null)
const currentTime = ref(0)
const duration = ref(0)

const canSearch = computed(() => query.value.trim().length >= 2)
const hasTracks = computed(() => tracks.value.length > 0)
const hasQueue = computed(() => queue.value.length > 0)
const currentTrack = computed(() => {
  if (currentIndex.value < 0 || currentIndex.value >= queue.value.length) return null
  return queue.value[currentIndex.value]
})
const progressPercent = computed(() => {
  if (!duration.value || duration.value <= 0) return 0
  return Math.min(100, Math.max(0, (currentTime.value / duration.value) * 100))
})

const formatDuration = (seconds?: number | null) => {
  if (!seconds || seconds < 1) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

const streamUrl = (videoId: string) => `${apiBase}/api/v1/stream/${encodeURIComponent(videoId)}.mp3`

const shuffle = <T,>(list: T[]): T[] => {
  const arr = [...list]
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[arr[i], arr[j]] = [arr[j]!, arr[i]!]
  }
  return arr
}

const setCurrentIndex = async (index: number, autoplay = true) => {
  if (index < 0 || index >= queue.value.length) return
  const track = queue.value[index]
  if (!track) return
  currentIndex.value = index
  audioSrc.value = streamUrl(track.videoId)
  currentTime.value = 0
  duration.value = track.duration ?? 0

  if (!autoplay) return
  await nextTick()
  try {
    await audioRef.value?.play()
  } catch {
    // autoplay может быть ограничен окружением
  }
}

const startTrackFromList = async (track: Track, list: Track[]) => {
  baseQueue.value = [...list]
  queue.value = [...list]
  mixEnabled.value = false
  const index = list.findIndex((t) => t.videoId === track.videoId)
  await setCurrentIndex(index >= 0 ? index : 0)
}

const playNext = async () => {
  if (currentIndex.value + 1 >= queue.value.length) return
  await setCurrentIndex(currentIndex.value + 1)
}

const playPrev = async () => {
  if (currentIndex.value - 1 < 0) return
  await setCurrentIndex(currentIndex.value - 1)
}

const togglePlay = async () => {
  const audio = audioRef.value
  if (!audio) return
  if (audio.paused) {
    await audio.play()
  } else {
    audio.pause()
  }
}

const toggleRepeat = () => {
  if (repeatMode.value === 'off') {
    repeatMode.value = 'all'
    return
  }
  if (repeatMode.value === 'all') {
    repeatMode.value = 'one'
    return
  }
  repeatMode.value = 'off'
}

const toggleMix = async () => {
  if (!baseQueue.value.length || !currentTrack.value) return
  const currentId = currentTrack.value.videoId
  mixEnabled.value = !mixEnabled.value

  if (mixEnabled.value) {
    const rest = baseQueue.value.filter((t) => t.videoId !== currentId)
    queue.value = [currentTrack.value, ...shuffle(rest)]
    currentIndex.value = 0
    return
  }

  queue.value = [...baseQueue.value]
  const index = queue.value.findIndex((t) => t.videoId === currentId)
  currentIndex.value = index >= 0 ? index : 0
}

const onEnded = async () => {
  if (!queue.value.length) return
  if (repeatMode.value === 'one') {
    await setCurrentIndex(currentIndex.value)
    return
  }
  if (currentIndex.value + 1 < queue.value.length) {
    await setCurrentIndex(currentIndex.value + 1)
    return
  }
  if (repeatMode.value === 'all') {
    await setCurrentIndex(0)
  }
}

const onSeek = async (event: Event) => {
  const audio = audioRef.value
  if (!audio || !duration.value) return
  const target = event.target as HTMLInputElement
  const nextPercent = Number(target.value)
  const nextTime = (nextPercent / 100) * duration.value
  audio.currentTime = nextTime
  currentTime.value = nextTime
}

const loadCharts = async () => {
  const resp = await fetch(`${apiBase}/api/v1/charts?country=EN&limit=20`)
  if (!resp.ok) {
    throw new Error(`Charts HTTP ${resp.status}`)
  }
  const data = await resp.json()
  tracks.value = Array.isArray(data.items) ? data.items : []
}

const loadLibrary = async () => {
  if (!telegramUserId.value) {
    info.value = 'Библиотека доступна только внутри Telegram.'
    return
  }
  loading.value = true
  error.value = ''
  info.value = ''
  try {
    const resp = await fetch(`${apiBase}/api/v1/library/${telegramUserId.value}?limit=200`)
    if (!resp.ok) {
      throw new Error(`Library HTTP ${resp.status}`)
    }
    const data = await resp.json()
    tracks.value = Array.isArray(data.items) ? data.items : []
    info.value = tracks.value.length > 0 ? 'Ваша библиотека' : 'В библиотеке пока нет треков.'
  } catch (e) {
    error.value = `Не удалось загрузить библиотеку: ${e instanceof Error ? e.message : 'unknown error'}`
  } finally {
    loading.value = false
  }
}

const searchTracks = async () => {
  if (!canSearch.value || loading.value) return
  loading.value = true
  error.value = ''
  info.value = ''
  tracks.value = []

  try {
    const q = encodeURIComponent(query.value.trim())
    const resp = await fetch(`${apiBase}/api/v1/search?q=${q}&limit=20`)
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`)
    }
    const data = await resp.json()
    tracks.value = Array.isArray(data.items) ? data.items : []
    if (tracks.value.length === 0) {
      await loadCharts()
      info.value = 'По вашему запросу ничего не найдено. Показаны чарты EN.'
    }
  } catch (e) {
    error.value = `Не удалось выполнить поиск: ${e instanceof Error ? e.message : 'unknown error'}`
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const tg = (window as any).Telegram?.WebApp
  if (tg) {
    tg.ready()
    tg.expand()
    telegramUserId.value = tg.initDataUnsafe?.user?.id ?? null
  }

  loading.value = true
  error.value = ''
  info.value = ''
  loadCharts()
    .then(() => {
      if (tracks.value.length > 0) {
        info.value = ''
      } else {
        info.value = 'Чарты сейчас недоступны. Попробуйте поиск по названию трека.'
      }
    })
    .catch((e) => {
      error.value = `Не удалось загрузить чарты: ${e instanceof Error ? e.message : 'unknown error'}`
    })
    .finally(() => {
      loading.value = false
    })
})
</script>

<template>
  <main class="page" :class="{ 'with-player': hasQueue }">
    <section class="panel hero">
      <div class="hero-row">
        <div>
          <p class="eyebrow">Music Streaming</p>
          <h1>Музыка в Telegram WebApp</h1>
        </div>
        <button class="library-btn" title="Открыть библиотеку" @click="loadLibrary">📚</button>
      </div>
      <p class="subtitle">Тап по треку запускает его и обновляет очередь.</p>
    </section>

    <section class="panel search-box">
      <form class="search-form" @submit.prevent="searchTracks">
        <input
          v-model="query"
          type="text"
          placeholder="Например: Lana Del Rey"
          autocomplete="off"
        />
        <button :disabled="!canSearch || loading" type="submit">
          {{ loading ? 'Ищем...' : 'Найти' }}
        </button>
      </form>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="info" class="info">{{ info }}</p>
    </section>

    <section class="panel list" v-if="hasTracks">
      <article
        class="track"
        v-for="track in tracks"
        :key="track.videoId"
        :class="{ active: currentTrack?.videoId === track.videoId }"
        @click="startTrackFromList(track, tracks)"
      >
        <div class="cover-wrap">
          <img v-if="track.thumbnail" :src="track.thumbnail" alt="cover" class="cover" />
          <div v-else class="cover fallback">♫</div>
        </div>

        <div class="meta">
          <h3>{{ track.title }}</h3>
          <p>{{ track.artist }}</p>
        </div>

        <div class="actions">
          <span>{{ formatDuration(track.duration) }}</span>
          <span class="tap">ID: {{ track.videoId }}</span>
        </div>
      </article>
    </section>

    <audio
      ref="audioRef"
      :src="audioSrc"
      preload="none"
      autoplay
      @ended="onEnded"
      @play="isPlaying = true"
      @pause="isPlaying = false"
      @timeupdate="currentTime = audioRef?.currentTime || 0"
      @loadedmetadata="duration = audioRef?.duration || duration"
    />

    <section v-if="hasQueue && currentTrack" class="bottom-player" :class="{ expanded: playerExpanded }">
      <div class="mini-player" @click="playerExpanded = true">
        <div class="mini-meta">
          <strong>{{ currentTrack.title }}</strong>
          <span>{{ currentTrack.artist }}</span>
        </div>
        <div class="mini-actions" @click.stop>
          <button class="mini-btn" :disabled="currentIndex <= 0" @click="playPrev">⏮</button>
          <button class="mini-btn play" @click="togglePlay">{{ isPlaying ? '⏸' : '▶' }}</button>
          <button class="mini-btn" :disabled="currentIndex >= queue.length - 1" @click="playNext">⏭</button>
        </div>
      </div>

      <div v-if="playerExpanded" class="sheet">
        <button class="close-sheet" @click="playerExpanded = false">Свернуть</button>

        <div class="sheet-header">
          <h2>{{ currentTrack.title }}</h2>
          <p>{{ currentTrack.artist }}</p>
        </div>

        <div class="wave-wrap" :style="{ '--progress': `${progressPercent}%` }">
          <input
            class="wave-seek"
            type="range"
            min="0"
            max="100"
            step="0.1"
            :value="progressPercent"
            @input="onSeek"
          />
          <div class="time-row">
            <span>{{ formatDuration(currentTime) }}</span>
            <span>{{ formatDuration(duration || currentTrack.duration) }}</span>
          </div>
        </div>

        <div class="sheet-controls">
          <button class="ctrl" :disabled="currentIndex <= 0" @click="playPrev">⏮</button>
          <button class="ctrl play" @click="togglePlay">{{ isPlaying ? 'Пауза' : 'Плей' }}</button>
          <button class="ctrl" :disabled="currentIndex >= queue.length - 1" @click="playNext">⏭</button>
        </div>

        <div class="mode-controls">
          <button class="mode-btn" :class="{ active: repeatMode !== 'off' }" @click="toggleRepeat">
            Репит: {{ repeatMode === 'off' ? 'OFF' : repeatMode === 'all' ? 'ALL' : 'ONE' }}
          </button>
          <button class="mode-btn" :class="{ active: mixEnabled }" @click="toggleMix">
            Микс: {{ mixEnabled ? 'ON' : 'OFF' }}
          </button>
        </div>

        <div class="queue">
          <p class="label">Очередь ({{ currentIndex + 1 }}/{{ queue.length }})</p>
          <div class="queue-list">
            <button
              v-for="(track, index) in queue"
              :key="`${track.videoId}-${index}`"
              class="queue-item"
              :class="{ active: index === currentIndex }"
              @click="setCurrentIndex(index)"
            >
              <span class="q-main">{{ track.artist }} — {{ track.title }}</span>
              <span class="q-time">{{ formatDuration(track.duration) }}</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>
