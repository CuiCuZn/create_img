<script setup lang="ts">
import { onMounted, ref } from "vue";
import PromptPanel from "../components/PromptPanel.vue";
import ImageDisplay from "../components/ImageDisplay.vue";
import { generate, getStyles } from "../api";
import type { ArtStyle, GenerateResponse, Shape } from "../types";

// 风格列表
const styles = ref<ArtStyle[]>([]);

// 表单状态
const prompt = ref("a mysterious rogue in a dimly lit city, narrow streets, distant bell tolling");
const negativePrompt = ref("");
const style = ref("None");
const shape = ref<Shape>("portrait");
const seed = ref(-1);
const guidanceScale = ref(7);
const referenceImage = ref<string | null>(null);
const count = ref(1);

// 结果状态
const loading = ref(false);
const result = ref<GenerateResponse | null>(null);
const error = ref("");

onMounted(async () => {
  try {
    styles.value = await getStyles();
  } catch (e) {
    console.error("加载风格失败", e);
  }
});

async function handleGenerate() {
  if (!prompt.value.trim() || loading.value) return;

  loading.value = true;
  error.value = "";
  result.value = null;

  try {
    result.value = await generate({
      prompt: prompt.value,
      negative_prompt: negativePrompt.value,
      seed: seed.value,
      shape: shape.value,
      guidance_scale: guidanceScale.value,
      style: style.value,
      reference_image: referenceImage.value,
      count: count.value,
    });
  } catch (e: any) {
    // axios 错误:优先取后端 detail
    const detail = e?.response?.data?.detail;
    error.value = detail || e?.message || "未知错误";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-6xl flex-col lg:flex-row lg:gap-6 lg:p-6">
    <!-- 左侧控制面板 -->
    <aside class="w-full shrink-0 lg:w-96">
      <div class="sticky top-0 rounded-xl bg-bg-card p-5 lg:top-6">
        <h1 class="mb-4 flex items-center gap-2 text-lg font-semibold text-gray-100">
          <span>🧝‍♀️</span> AI 角色生成器
        </h1>
        <PromptPanel
          v-model:prompt="prompt"
          v-model:negativePrompt="negativePrompt"
          v-model:style="style"
          v-model:shape="shape"
          v-model:seed="seed"
          v-model:guidanceScale="guidanceScale"
          v-model:referenceImage="referenceImage"
          v-model:count="count"
          :styles="styles"
          :loading="loading"
          @generate="handleGenerate"
        />
      </div>
    </aside>

    <!-- 右侧展示区 -->
    <main class="mt-4 flex-1 rounded-xl bg-bg-card p-5 lg:mt-0">
      <ImageDisplay
        :loading="loading"
        :result="result"
        :error="error"
        :count="count"
        @regenerate="handleGenerate"
      />
    </main>
  </div>
</template>
