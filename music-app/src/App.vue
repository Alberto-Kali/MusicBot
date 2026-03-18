<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

type Track = {
  videoId: string
  title: string
  artist: string
  duration?: number | null
  thumbnail?: string | null
}

type DirectStreamResponse = {
  videoId: string
  stream_url: string
  duration?: number | null
  mime_type?: string | null
  expires_in?: number | null
  source?: string | null
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

const query = ref('')
const loading = ref(false)
const error = ref('')
const info = ref('')
const tracks = ref<Track[]>([])
const telegramInitData = ref('')
const telegramVerified = ref(false)
const telegramAuthError = ref('')
const libraryMode = ref(false)
const tgIdentity = ref('гость')
const trackInLibrary = ref(false)

const queue = ref<Track[]>([])
const baseQueue = ref<Track[]>([])
const currentIndex = ref(-1)
const audioSrc = ref('')
const isPlaying = ref(false)
const playerExpanded = ref(false)
const queueDrawerOpen = ref(false)
const repeatMode = ref<'off' | 'all' | 'one'>('off')
const mixEnabled = ref(false)
const audioRef = ref<HTMLAudioElement | null>(null)
const currentTime = ref(0)
const duration = ref(0)
const audioSourceMode = ref<'direct' | 'proxy'>('proxy')
const streamLoadToken = ref(0)

const canSearch = computed(() => query.value.trim().length >= 2)
const hasTracks = computed(() => tracks.value.length > 0)
const hasQueue = computed(() => queue.value.length > 0)
const listTitle = computed(() => {
  if (libraryMode.value) return 'Ваша библиотека'
  return info.value || 'Топ чартов EN'
})
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
const directStreamUrl = (videoId: string) => `${apiBase}/api/v1/direct-stream/${encodeURIComponent(videoId)}`

const parseTelegramUserFromInitData = (initData: string): { id?: number; username?: string } => {
  try {
    const params = new URLSearchParams(initData)
    const rawUser = params.get('user')
    if (!rawUser) return {}
    const user = JSON.parse(rawUser)
    return {
      id: typeof user.id === 'number' ? user.id : undefined,
      username: typeof user.username === 'string' ? user.username : undefined,
    }
  } catch {
    return {}
  }
}

const shuffle = <T,>(list: T[]): T[] => {
  const arr = [...list]
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[arr[i], arr[j]] = [arr[j]!, arr[i]!]
  }
  return arr
}

const resolvePrimaryAudioUrl = async (videoId: string): Promise<string> => {
  const resp = await fetch(directStreamUrl(videoId))
  if (!resp.ok) {
    throw new Error(`Direct stream HTTP ${resp.status}`)
  }
  const data = (await resp.json()) as DirectStreamResponse
  if (!data.stream_url) {
    throw new Error('Direct stream URL is missing')
  }
  return data.stream_url
}

const setAudioSource = async (src: string, mode: 'direct' | 'proxy', autoplay: boolean) => {
  audioSourceMode.value = mode
  audioSrc.value = src
  if (!autoplay) return
  await nextTick()
  try {
    await audioRef.value?.play()
  } catch {
    // autoplay может быть ограничен окружением
  }
}

const setCurrentIndex = async (index: number, autoplay = true) => {
  if (index < 0 || index >= queue.value.length) return
  const track = queue.value[index]
  if (!track) return
  const loadToken = streamLoadToken.value + 1
  streamLoadToken.value = loadToken
  currentIndex.value = index
  currentTime.value = 0
  duration.value = track.duration ?? 0
  trackInLibrary.value = false
  try {
    const primaryUrl = await resolvePrimaryAudioUrl(track.videoId)
    if (streamLoadToken.value !== loadToken) return
    await setAudioSource(primaryUrl, 'direct', autoplay)
  } catch {
    if (streamLoadToken.value !== loadToken) return
    await setAudioSource(streamUrl(track.videoId), 'proxy', autoplay)
  }
  syncCurrentTrackLibraryState()
}

const onAudioError = async () => {
  const track = currentTrack.value
  if (!track) return
  if (audioSourceMode.value === 'direct') {
    await setAudioSource(streamUrl(track.videoId), 'proxy', true)
    return
  }
  error.value = 'Не удалось воспроизвести трек даже через резервный backend stream.'
}

const syncCurrentTrackLibraryState = async () => {
  if (!currentTrack.value || !telegramInitData.value || !telegramVerified.value) {
    trackInLibrary.value = false
    return
  }
  try {
    const resp = await fetch(`${apiBase}/api/v1/library/contains`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: telegramInitData.value, video_id: currentTrack.value.videoId }),
    })
    if (!resp.ok) throw new Error(`Contains HTTP ${resp.status}`)
    const data = await resp.json()
    trackInLibrary.value = Boolean(data.in_library)
  } catch {
    trackInLibrary.value = false
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

const onSeek = (event: Event) => {
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
  libraryMode.value = false
}

const loadLibrary = async (): Promise<boolean> => {
  if (!telegramInitData.value || !telegramVerified.value) {
    info.value = telegramAuthError.value || 'Библиотека доступна только внутри Telegram.'
    return false
  }
  loading.value = true
  error.value = ''
  info.value = ''
  try {
    const resp = await fetch(`${apiBase}/api/v1/library/me?limit=200`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: telegramInitData.value }),
    })
    if (!resp.ok) {
      throw new Error(`Library HTTP ${resp.status}`)
    }
    const data = await resp.json()
    tracks.value = Array.isArray(data.items) ? data.items : []
    info.value = tracks.value.length > 0 ? 'Ваша библиотека' : 'В библиотеке пока нет треков.'
    libraryMode.value = true
    return true
  } catch (e) {
    error.value = `Не удалось загрузить библиотеку: ${e instanceof Error ? e.message : 'unknown error'}`
    return false
  } finally {
    loading.value = false
  }
}

const toggleLibraryView = async () => {
  if (loading.value) return
  error.value = ''
  if (libraryMode.value) {
    loading.value = true
    info.value = ''
    try {
      await loadCharts()
      info.value = tracks.value.length ? 'Топ чартов EN' : 'Чарты сейчас недоступны.'
    } catch (e) {
      error.value = `Не удалось загрузить чарты: ${e instanceof Error ? e.message : 'unknown error'}`
    } finally {
      loading.value = false
    }
    return
  }
  await loadLibrary()
}

const addCurrentTrackToLibrary = async () => {
  if (!currentTrack.value) return
  if (!telegramInitData.value || !telegramVerified.value) {
    info.value = telegramAuthError.value || 'Добавление в библиотеку доступно только внутри Telegram.'
    return
  }

  try {
    const track = currentTrack.value
    const endpoint = trackInLibrary.value ? '/api/v1/library/remove' : '/api/v1/library/add'
    const resp = await fetch(`${apiBase}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        init_data: telegramInitData.value,
        video_id: track.videoId,
        title: track.title,
        artist: track.artist,
        duration: track.duration,
        thumbnail: track.thumbnail,
      }),
    })
    if (!resp.ok) {
      throw new Error(`Library toggle HTTP ${resp.status}`)
    }
    const data = await resp.json()
    if (trackInLibrary.value) {
      trackInLibrary.value = false
      info.value = data.removed ? 'Трек удалён из библиотеки.' : 'Трек уже был удалён.'
    } else {
      trackInLibrary.value = true
      info.value = data.added ? 'Трек добавлен в библиотеку.' : 'Трек уже есть в библиотеке.'
    }
  } catch (e) {
    error.value = `Не удалось добавить трек: ${e instanceof Error ? e.message : 'unknown error'}`
  }
}

const verifyTelegram = async () => {
  if (!telegramInitData.value) {
    telegramVerified.value = false
    telegramAuthError.value = 'Telegram initData не получен. Откройте WebApp из кнопки бота.'
    return
  }
  try {
    const resp = await fetch(`${apiBase}/api/v1/auth/telegram`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: telegramInitData.value }),
    })
    if (!resp.ok) {
      const text = await resp.text()
      throw new Error(`Auth HTTP ${resp.status}: ${text}`)
    }
    await resp.json()
    telegramVerified.value = true
    telegramAuthError.value = ''
    syncCurrentTrackLibraryState()
  } catch (e) {
    telegramVerified.value = false
    telegramAuthError.value = `Авторизация Telegram не прошла: ${e instanceof Error ? e.message : 'unknown error'}`
  }
}

const pickInitDataWithRetry = async (): Promise<string> => {
  for (let i = 0; i < 20; i += 1) {
    const value = (window as any).Telegram?.WebApp?.initData || ''
    if (value) return value
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  return ''
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
    libraryMode.value = false
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
  }

  pickInitDataWithRetry()
    .then((value) => {
      telegramInitData.value = value
      const user = parseTelegramUserFromInitData(value)
      tgIdentity.value = user.username ? `${user.username} | ${user.id ?? ''}` : `${user.id ?? 'гость'}`
      return verifyTelegram()
    })
    .catch(() => {
      telegramVerified.value = false
      telegramAuthError.value = 'Не удалось получить Telegram initData.'
    })

  loading.value = true
  error.value = ''
  info.value = ''
  loadCharts()
    .then(() => {
      info.value = tracks.value.length ? 'Топ чартов EN' : 'Чарты сейчас недоступны. Попробуйте поиск по названию трека.'
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
        <button class="library-btn" :class="{ active: libraryMode }" title="Переключить библиотеку" @click="toggleLibraryView">
          📚
        </button>
      </div>
      <p class="subtitle">Добро пожаловать {{ tgIdentity }}</p>
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
    </section>

    <section class="panel list" v-if="hasTracks">
      <h2 class="list-title">{{ listTitle }}</h2>
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
      @error="onAudioError"
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

        <div class="sheet-cover">
          <img v-if="currentTrack.thumbnail" :src="currentTrack.thumbnail" alt="cover" class="sheet-cover-img" />
          <div v-else class="sheet-cover-img sheet-cover-fallback">♫</div>
        </div>

        <div class="sheet-header">
          <h2>{{ currentTrack.title }}</h2>
          <p>{{ currentTrack.artist }}</p>
        </div>

        <div class="wave-wrap" :style="{ '--progress': `${progressPercent}%` }">
          <input class="wave-seek" type="range" min="0" max="100" step="0.1" :value="progressPercent" @input="onSeek" />
          <div class="time-row">
            <span>{{ formatDuration(currentTime) }}</span>
            <span>{{ formatDuration(duration || currentTrack.duration) }}</span>
          </div>
        </div>

        <div class="sheet-controls icons-only">
          <button class="ctrl" :disabled="currentIndex <= 0" @click="playPrev" title="Предыдущий">⏮</button>
          <button class="ctrl play" @click="togglePlay" title="Плей/Пауза">{{ isPlaying ? '⏸' : '▶' }}</button>
          <button class="ctrl" :disabled="currentIndex >= queue.length - 1" @click="playNext" title="Следующий">⏭</button>
        </div>

        <div class="mode-controls icons-only">
          <button class="mode-btn" :class="{ active: repeatMode !== 'off' }" @click="toggleRepeat" title="Репит">
            {{ repeatMode === 'one' ? '🔂' : '🔁' }}
          </button>
          <button class="mode-btn" :class="{ active: mixEnabled }" @click="toggleMix" title="Микс">🔀</button>
          <button class="mode-btn heart-btn" :class="{ active: trackInLibrary }" @click="addCurrentTrackToLibrary" :title="trackInLibrary ? 'Удалить из библиотеки' : 'Добавить в библиотеку'">
            {{ trackInLibrary ? '❤️' : '💔' }}
          </button>
          <button class="mode-btn" @click="queueDrawerOpen = true" title="Очередь">☰</button>
        </div>
      </div>
    </section>

    <div v-if="queueDrawerOpen" class="drawer-backdrop" @click="queueDrawerOpen = false" />
    <aside class="queue-drawer" :class="{ open: queueDrawerOpen }">
      <div class="queue-head">
        <strong>Очередь</strong>
        <button class="close-drawer" @click="queueDrawerOpen = false">✕</button>
      </div>
      <div class="queue-list">
        <button
          v-for="(track, index) in queue"
          :key="`${track.videoId}-${index}`"
          class="queue-item"
          :class="{ active: index === currentIndex }"
          @click="setCurrentIndex(index); queueDrawerOpen = false"
        >
          <span class="q-main">{{ track.artist }} — {{ track.title }}</span>
          <span class="q-time">{{ formatDuration(track.duration) }}</span>
        </button>
      </div>
    </aside>
  </main>
</template>
