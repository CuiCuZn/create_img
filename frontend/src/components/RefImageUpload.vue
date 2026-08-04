<script setup lang="ts">
import { ref } from "vue";
import { fileToDataUrl } from "../api";

const props = defineProps<{
  /** 当前参考图 data URL(v-model)。 */
  modelValue: string | null;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string | null): void;
}>();

const isDragging = ref(false);
const errorMsg = ref("");

async function handleFile(file: File) {
  errorMsg.value = "";
  // 校验类型与大小
  if (!file.type.startsWith("image/")) {
    errorMsg.value = "请上传图片文件";
    return;
  }
  if (file.size > 8 * 1024 * 1024) {
    errorMsg.value = "图片不能超过 8MB";
    return;
  }
  try {
    const dataUrl = await fileToDataUrl(file);
    emit("update:modelValue", dataUrl);
  } catch {
    errorMsg.value = "读取图片失败";
  }
}

function onDrop(e: DragEvent) {
  isDragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) handleFile(file);
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) handleFile(file);
  input.value = ""; // 允许重复选同一文件
}

function clear() {
  emit("update:modelValue", null);
  errorMsg.value = "";
}
</script>

<template>
  <div>
    <div
      v-if="!props.modelValue"
      class="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-gray-600 p-6 transition-colors hover:border-accent"
      :class="{ 'border-accent bg-bg-hover': isDragging }"
      @dragover.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
      @drop.prevent="onDrop"
    >
      <svg class="h-8 w-8 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
      </svg>
      <p class="text-sm text-gray-400">拖拽图片到此处或</p>
      <label class="cursor-pointer rounded-md bg-bg-hover px-3 py-1.5 text-sm text-gray-200 hover:bg-accent hover:text-white">
        选择文件
        <input type="file" accept="image/*" class="hidden" @change="onPick" />
      </label>
      <p class="text-xs text-gray-600">参考图用于图生图 · 最大 8MB</p>
    </div>

    <div v-else class="relative">
      <img :src="props.modelValue" alt="参考图" class="max-h-48 w-full rounded-lg object-contain" />
      <button
        class="absolute right-2 top-2 rounded-full bg-black/60 p-1.5 text-white hover:bg-red-600"
        title="移除参考图"
        @click="clear"
      >
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <p v-if="errorMsg" class="mt-1 text-xs text-red-400">{{ errorMsg }}</p>
  </div>
</template>
