<script setup lang="ts">
import { ref } from "vue";
import type { ArtStyle, Shape } from "../types";
import RefImageUpload from "./RefImageUpload.vue";
import { styleLabel } from "../api/styleLabels";

const props = defineProps<{
  styles: ArtStyle[];
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "generate"): void;
}>();

// 表单状态(通过 defineModel 双向绑定到父组件)
const prompt = defineModel<string>("prompt", { default: "" });
const negativePrompt = defineModel<string>("negativePrompt", { default: "" });
const style = defineModel<string>("style", { default: "None" });
const shape = defineModel<Shape>("shape", { default: "square" });
const seed = defineModel<number>("seed", { default: -1 });
const guidanceScale = defineModel<number>("guidanceScale", { default: 7 });
const referenceImage = defineModel<string | null>("referenceImage", { default: null });
const count = defineModel<number>("count", { default: 1 });

const showAdvanced = ref(false);

const shapes: { value: Shape; label: string; ratio: string }[] = [
  { value: "portrait", label: "竖图", ratio: "512 × 768" },
  { value: "square", label: "方图", ratio: "768 × 768" },
  { value: "landscape", label: "横图", ratio: "768 × 512" },
];

const counts = [1, 2, 4];

function randomSeed() {
  seed.value = Math.floor(Math.random() * 9_999_999);
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 提示词 -->
    <div>
      <label class="mb-1.5 block text-sm font-medium text-gray-300">📝 描述</label>
      <textarea
        v-model="prompt"
        rows="4"
        placeholder="描述你想要的角色或画面,如:hell spawn rogue, dirk, in a dimly lit city..."
        class="w-full resize-y rounded-lg border border-gray-700 bg-bg p-3 text-sm text-gray-100 placeholder-gray-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      ></textarea>
      <p class="mt-1 text-xs text-gray-600">
        支持 perchance 语法:<code class="text-accent">{a|b|c}</code> 随机选词、
        <code class="text-accent">(text)</code> 加权
      </p>
    </div>

    <!-- 风格 -->
    <div>
      <label class="mb-1.5 block text-sm font-medium text-gray-300">🎨 艺术风格</label>
      <select
        v-model="style"
        class="w-full rounded-lg border border-gray-700 bg-bg p-2.5 text-sm text-gray-100 focus:border-accent focus:outline-none"
      >
        <option v-for="s in props.styles" :key="s.name" :value="s.name">{{ styleLabel(s.name) }}</option>
      </select>
    </div>

    <!-- 形状 -->
    <div>
      <label class="mb-1.5 block text-sm font-medium text-gray-300">🖼️ 形状</label>
      <div class="grid grid-cols-3 gap-2">
        <button
          v-for="sh in shapes"
          :key="sh.value"
          class="rounded-lg border p-2.5 text-center transition-colors"
          :class="shape === sh.value
            ? 'border-accent bg-accent/20 text-white'
            : 'border-gray-700 bg-bg text-gray-400 hover:border-gray-500'"
          @click="shape = sh.value"
        >
          <div class="text-sm font-medium">{{ sh.label }}</div>
          <div class="text-xs text-gray-500">{{ sh.ratio }}</div>
        </button>
      </div>
    </div>

    <!-- 数量 -->
    <div>
      <label class="mb-1.5 block text-sm font-medium text-gray-300">🔢 数量</label>
      <div class="grid grid-cols-3 gap-2">
        <button
          v-for="c in counts"
          :key="c"
          class="rounded-lg border p-2 text-center text-sm font-medium transition-colors"
          :class="count === c
            ? 'border-accent bg-accent/20 text-white'
            : 'border-gray-700 bg-bg text-gray-400 hover:border-gray-500'"
          @click="count = c"
        >
          {{ c }} 张
        </button>
      </div>
    </div>

    <!-- 高级选项 -->
    <div>
      <button
        class="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
        @click="showAdvanced = !showAdvanced"
      >
        <svg
          class="h-4 w-4 transition-transform"
          :class="{ 'rotate-90': showAdvanced }"
          fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        高级选项
      </button>

      <div v-show="showAdvanced" class="mt-3 flex flex-col gap-4 border-l-2 border-gray-800 pl-4">
        <!-- 负面提示词 -->
        <div>
          <label class="mb-1.5 block text-sm font-medium text-gray-300">负面提示词</label>
          <textarea
            v-model="negativePrompt"
            rows="2"
            placeholder="不希望出现的内容,如:blurry, low quality, deformed"
            class="w-full resize-y rounded-lg border border-gray-700 bg-bg p-2.5 text-sm text-gray-100 placeholder-gray-600 focus:border-accent focus:outline-none"
          ></textarea>
        </div>

        <!-- 种子 -->
        <div>
          <label class="mb-1.5 block text-sm font-medium text-gray-300">种子</label>
          <div class="flex gap-2">
            <input
              v-model.number="seed"
              type="number"
              class="flex-1 rounded-lg border border-gray-700 bg-bg p-2.5 text-sm text-gray-100 focus:border-accent focus:outline-none"
            />
            <button
              class="rounded-lg bg-bg-hover px-3 text-sm text-gray-300 hover:bg-accent hover:text-white"
              title="随机种子"
              @click="randomSeed"
            >🎲</button>
          </div>
          <p class="mt-1 text-xs text-gray-600">-1 表示随机</p>
        </div>

        <!-- 引导系数 -->
        <div>
          <label class="mb-1.5 flex items-center justify-between text-sm font-medium text-gray-300">
            <span>引导系数</span>
            <span class="text-accent">{{ guidanceScale.toFixed(1) }}</span>
          </label>
          <input
            v-model.number="guidanceScale"
            type="range" min="1" max="20" step="0.5"
            class="w-full"
          />
          <p class="mt-1 text-xs text-gray-600">数值越高越贴近提示词,通常 5-10</p>
        </div>

        <!-- 图生图参考图 -->
        <div>
          <label class="mb-1.5 block text-sm font-medium text-gray-300">🖼️ 参考图(图生图)</label>
          <RefImageUpload v-model="referenceImage" />
        </div>
      </div>
    </div>

    <!-- 生成按钮 -->
    <button
      class="mt-2 flex items-center justify-center gap-2 rounded-lg bg-accent py-3 text-base font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
      :disabled="props.loading || !prompt.trim()"
      @click="emit('generate')"
    >
      <svg v-if="props.loading" class="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span v-else>✨</span>
      {{ props.loading ? "生成中..." : "生成" }}
    </button>
  </div>
</template>
