<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import type { GenerateResponse, ImageItem } from "../types";
import { toDataUrl } from "../api";
import { styleLabel } from "../api/styleLabels";

const props = defineProps<{
  loading: boolean;
  result: GenerateResponse | null;
  error: string;
  count: number;
}>();

const emit = defineEmits<{
  (e: "regenerate"): void;
  (e: "download"): void;
}>();

// 每张图的 NSFW 已确认查看状态,按图片 index 跟踪
const nsfwRevealed = ref<Set<number>>(new Set());
watch(
  () => props.result,
  () => {
    nsfwRevealed.value = new Set();
  }
);

// 灯箱:放大查看。null=关闭,数字=当前查看的图片 index
const lightboxIdx = ref<number | null>(null);

function openLightbox(idx: number) {
  lightboxIdx.value = idx;
}
function closeLightbox() {
  lightboxIdx.value = null;
}
function prevImage() {
  if (lightboxIdx.value === null || !props.result) return;
  const n = props.result.images.length;
  lightboxIdx.value = (lightboxIdx.value - 1 + n) % n;
}
function nextImage() {
  if (lightboxIdx.value === null || !props.result) return;
  const n = props.result.images.length;
  lightboxIdx.value = (lightboxIdx.value + 1) % n;
}
function onKeydown(e: KeyboardEvent) {
  if (lightboxIdx.value === null) return;
  if (e.key === "Escape") closeLightbox();
  else if (e.key === "ArrowLeft") prevImage();
  else if (e.key === "ArrowRight") nextImage();
}
onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));

const lightboxItem = computed(() => {
  if (lightboxIdx.value === null || !props.result) return null;
  return props.result.images[lightboxIdx.value] ?? null;
});

function dataUrlOf(item: ImageItem): string {
  return toDataUrl(item.image, item.file_extension);
}

function downloadNameOf(item: ImageItem): string {
  return `perchance_${item.seed}.${item.file_extension}`;
}

// 网格列数:1 张单列,2 张双列,4 张双列
const gridCols = computed(() => {
  const n = props.result?.images.length ?? 1;
  if (n <= 1) return "grid-cols-1";
  return "grid-cols-2";
});

function toggleNsfw(idx: number) {
  const s = new Set(nsfwRevealed.value);
  if (s.has(idx)) s.delete(idx);
  else s.add(idx);
  nsfwRevealed.value = s;
}
</script>

<template>
  <div class="flex h-full flex-col items-center justify-center">
    <!-- 空状态 -->
    <div v-if="!props.loading && !props.result && !props.error" class="text-center">
      <div class="mb-4 text-6xl opacity-30">🎨</div>
      <p class="text-gray-500">输入描述,选择风格,点击生成</p>
      <p class="mt-1 text-sm text-gray-600">生图约需 5-15 秒/张</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="props.error && !props.loading" class="max-w-md text-center">
      <div class="mb-3 text-5xl">⚠️</div>
      <p class="mb-1 font-medium text-red-400">生成失败</p>
      <p class="text-sm text-gray-500">{{ props.error }}</p>
      <button
        class="mt-4 rounded-lg bg-bg-hover px-4 py-2 text-sm text-gray-200 hover:bg-accent hover:text-white"
        @click="emit('regenerate')"
      >重试</button>
    </div>

    <!-- 加载状态 -->
    <div v-else-if="props.loading" class="w-full text-center">
      <!-- 骨架屏网格 -->
      <div class="mx-auto grid max-w-xl gap-3" :class="props.count === 1 ? 'grid-cols-1' : 'grid-cols-2'">
        <div
          v-for="i in props.count"
          :key="i"
          class="aspect-square animate-pulse rounded-xl bg-bg-card"
        ></div>
      </div>
      <div class="mt-4 flex items-center justify-center gap-2 text-gray-400">
        <svg class="h-5 w-5 animate-spin text-accent" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span class="text-sm">正在生成 {{ props.count }} 张图片...</span>
      </div>
      <p class="mt-2 text-xs text-gray-600">浏览器代理正在与 perchance 交互,请耐心等待</p>
    </div>

    <!-- 结果展示 -->
    <div v-else-if="props.result" class="flex w-full flex-col items-center">
      <!-- 图片网格 -->
      <div class="mx-auto grid max-w-2xl gap-3" :class="gridCols">
        <div
          v-for="(item, idx) in props.result.images"
          :key="idx"
          class="relative"
        >
          <img
            :src="dataUrlOf(item)"
            :alt="`生成结果 ${idx + 1}`"
            class="w-full cursor-zoom-in rounded-xl border border-gray-800 object-contain transition-transform hover:opacity-90"
            @click="openLightbox(idx)"
          />
          <!-- 单图种子标签 -->
          <span class="absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-gray-300">
            #{{ idx + 1 }} · {{ item.seed }}
          </span>
          <!-- NSFW 遮罩 -->
          <div
            v-if="item.maybe_nsfw && !nsfwRevealed.has(idx)"
            class="absolute inset-0 flex flex-col items-center justify-center rounded-xl bg-black/80 backdrop-blur-sm"
          >
            <span class="text-3xl">🔞</span>
            <p class="mt-2 text-sm text-gray-300">可能含敏感内容</p>
            <button
              class="mt-3 rounded-md bg-bg-hover px-3 py-1.5 text-xs text-gray-200 hover:bg-accent"
              @click="toggleNsfw(idx)"
            >
              点击查看
            </button>
          </div>
        </div>
      </div>

      <!-- 元数据 -->
      <div class="mt-4 flex flex-wrap items-center justify-center gap-3 text-xs text-gray-500">
        <span class="rounded-md bg-bg-card px-2 py-1">{{ props.result.images.length }} 张</span>
        <span class="rounded-md bg-bg-card px-2 py-1">{{ props.result.images[0]?.width }} × {{ props.result.images[0]?.height }}</span>
        <span class="rounded-md bg-bg-card px-2 py-1">风格: {{ styleLabel(props.result.style) }}</span>
      </div>

      <!-- 操作按钮 -->
      <div class="mt-4 flex gap-3">
        <a
          v-for="(item, idx) in props.result.images"
          :key="'dl'+idx"
          :href="dataUrlOf(item)"
          :download="downloadNameOf(item)"
          class="flex items-center gap-1.5 rounded-lg bg-bg-hover px-4 py-2 text-sm text-gray-200 hover:bg-accent hover:text-white"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
          下载 #{{ idx + 1 }}
        </a>
        <button
          class="flex items-center gap-1.5 rounded-lg bg-bg-hover px-4 py-2 text-sm text-gray-200 hover:bg-accent hover:text-white"
          @click="emit('regenerate')"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
          </svg>
          重新生成
        </button>
      </div>
    </div>

    <!-- 灯箱:点击图片放大查看 -->
    <Teleport to="body">
      <div
        v-if="lightboxIdx !== null && lightboxItem"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-sm"
        @click="closeLightbox"
      >
        <!-- 关闭按钮 -->
        <button
          class="absolute right-4 top-4 z-10 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="关闭 (Esc)"
          @click.stop="closeLightbox"
        >
          <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <!-- 上一张 -->
        <button
          v-if="props.result && props.result.images.length > 1"
          class="absolute left-4 z-10 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="上一张 (←)"
          @click.stop="prevImage"
        >
          <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        <!-- 下一张 -->
        <button
          v-if="props.result && props.result.images.length > 1"
          class="absolute right-4 z-10 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          title="下一张 (→)"
          @click.stop="nextImage"
        >
          <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>

        <!-- 放大的图片 -->
        <img
          :src="dataUrlOf(lightboxItem)"
          :alt="`放大查看`"
          class="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
          @click.stop
        />

        <!-- 底部信息 -->
        <div class="absolute bottom-6 left-1/2 -translate-x-1/2 rounded-lg bg-black/60 px-4 py-2 text-center text-sm text-gray-300">
          <span>#{{ (lightboxIdx ?? 0) + 1 }} / {{ props.result?.images.length }}</span>
          <span class="mx-2 text-gray-600">·</span>
          <span>种子 {{ lightboxItem.seed }}</span>
          <span class="mx-2 text-gray-600">·</span>
          <span>{{ lightboxItem.width }} × {{ lightboxItem.height }}</span>
          <a
            :href="dataUrlOf(lightboxItem)"
            :download="downloadNameOf(lightboxItem)"
            class="ml-3 inline-flex items-center gap-1 text-accent hover:underline"
            @click.stop
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            下载
          </a>
        </div>
      </div>
    </Teleport>
  </div>
</template>
