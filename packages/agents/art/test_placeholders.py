"""
Yishun Again — placeholder image test generation
================================================
Generates ONE test image per classification (heart / clown / dagger) using the
existing LoRA + Modal setup, and uploads each to R2 under `placeholders/test/`.

This is a NON-PRODUCTION path. It never writes to `pixel-art/` — output lives
under `placeholders/test/` so the operator can review before promoting.

SELF-CONTAINED by design: Modal uploads only the entrypoint file to the
container, not the whole art/ directory, so the Modal image, R2 client, and
SDXL + LoRA loading logic are copied inline from generate_pixel_art.py rather
than imported.

Usage:
    modal run art/test_placeholders.py

Prerequisites
-------------
1.  modal token new
2.  Modal secret "cloudflare-r2"
3.  LoRA weights already uploaded to R2 (lora/yishunagain_v2.safetensors)
"""

import io
import os
from pathlib import Path

import modal

# ── Config ──────────────────────────────────────────────────────────────────
LORA_R2_KEY    = "lora/yishunagain_v2.safetensors"
R2_PUBLIC_BASE = "https://assets.yishunagain.com"
BASE_MODEL     = "stabilityai/stable-diffusion-xl-base-1.0"
TRIGGER_WORD   = "yishunpixel"

LORA_SCALE          = 0.85
NUM_INFERENCE_STEPS = 30
WIDTH               = 1024
HEIGHT              = 1024

NEGATIVE_PROMPT = (
    "photorealistic, 3d render, photograph, blurry, people faces, realistic, "
    "cartoon, anime, low quality, text, watermark"
)

# Three test cases: (classification, prompt, r2_key)
TEST_CASES: list[tuple[str, str, str]] = [
    (
        "heart",
        "yishunpixel, HD-2D pixel art, Octopath Traveler style, Singaporean man "
        "early 30s in blue collared shirt and black trousers, laughing and shaking "
        "hands with elderly HDB neighbour at void deck, red and gold decorations "
        "hanging overhead, warm golden lantern light, festive community atmosphere, "
        "expressive joyful faces, isometric view, detailed background, masterpiece, "
        "best quality",
        "placeholders/test/good-vibes-test.png",
    ),
    (
        "clown",
        "yishunpixel, HD-2D pixel art, Octopath Traveler style, Singaporean man "
        "early 30s in blue collared shirt and black trousers, standing frozen in "
        "disbelief, arms raised, staring at overturned bicycle blocking HDB void "
        "deck entrance, chickens scattered everywhere, bystanders pointing and "
        "laughing, bright afternoon lighting, exaggerated shocked expression, "
        "isometric view, detailed background, masterpiece, best quality",
        "placeholders/test/absurdities-test.png",
    ),
    (
        "dagger",
        "yishunpixel, HD-2D pixel art, Octopath Traveler style, Singaporean man "
        "early 30s in blue collared shirt and black trousers, standing alone under "
        "flickering fluorescent light in HDB void deck, police tape behind him, "
        "shadows deep, face tense and haunted, phone gripped tightly, ominous night "
        "atmosphere, dramatic side lighting, isometric view, detailed background, "
        "masterpiece, best quality",
        "placeholders/test/dark-events-test.png",
    ),
]

# ── Modal resources (copied from generate_pixel_art.py) ──────────────────────
app         = modal.App("yishun-pixel-art-placeholder-test")
model_cache = modal.Volume.from_name("yishun-hf-cache", create_if_missing=True)

inference_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04",
        add_python="3.11",
    )
    .pip_install(
        "torch==2.1.2",
        "torchvision==0.16.2",
        extra_index_url="https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "xformers==0.0.23.post1",
        extra_index_url="https://download.pytorch.org/whl/cu121",
    )
    .pip_install(
        "numpy<2",
        "huggingface_hub<0.24.0",
        "diffusers==0.26.3",
        "transformers==4.38.2",
        "accelerate==0.27.2",
        "safetensors",
        "Pillow>=10.0.0",
        "boto3",
    )
)


def _r2_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CF_R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


# ── Inference class — model loaded once per container ────────────────────────
@app.cls(
    image=inference_image,
    gpu="A10G",
    timeout=300,
    scaledown_window=120,
    volumes={"/cache": model_cache},
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
class PlaceholderTester:
    @modal.enter()
    def load_model(self):
        import torch
        from diffusers import StableDiffusionXLPipeline

        # HF downloads go to the cached volume — only happens on first container start
        os.environ["HF_HOME"]            = "/cache/huggingface"
        os.environ["TRANSFORMERS_CACHE"] = "/cache/huggingface"

        # Download LoRA weights from R2 to /tmp — always re-download (cache-bust)
        lora_local = "/tmp/yishunagain_v2.safetensors"
        if Path(lora_local).exists():
            Path(lora_local).unlink()
            print(f"[load] Cache-busted stale {lora_local}")
        bucket = os.environ["CF_R2_BUCKET_NAME"]
        print(f"[load] Downloading LoRA from R2: {LORA_R2_KEY}")
        _r2_client().download_file(bucket, LORA_R2_KEY, lora_local)
        print(f"[load] LoRA downloaded ({Path(lora_local).stat().st_size / 1e6:.1f} MB)")

        print(f"[load] Loading SDXL pipeline from {BASE_MODEL}…")
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            BASE_MODEL,
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )
        self.pipe.load_lora_weights(lora_local)
        self.pipe.enable_xformers_memory_efficient_attention()
        self.pipe = self.pipe.to("cuda")
        print("[load] Pipeline ready.")

    @modal.method()
    def generate_test(self, classification: str, prompt: str, r2_key: str) -> str:
        """Generate one test image at 1024x1024 and upload to R2 at r2_key."""
        import torch

        print(f"\n[gen:{classification}] Prompt: {prompt}")

        with torch.inference_mode():
            result = self.pipe(
                prompt=prompt,
                negative_prompt=NEGATIVE_PROMPT,
                num_inference_steps=NUM_INFERENCE_STEPS,
                guidance_scale=7.5,
                width=WIDTH,
                height=HEIGHT,
                cross_attention_kwargs={"scale": LORA_SCALE},
            )

        image = result.images[0]  # 1024x1024 — no resize for test placeholders

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        bucket = os.environ["CF_R2_BUCKET_NAME"]
        print(f"[gen:{classification}] Uploading to R2: {r2_key}")
        _r2_client().put_object(
            Bucket=bucket,
            Key=r2_key,
            Body=buf.getvalue(),
            ContentType="image/png",
        )

        url = f"{R2_PUBLIC_BASE}/{r2_key}"
        print(f"[gen:{classification}] Done: {url}")
        return url


# ── Local entrypoint — runs all 3 sequentially ───────────────────────────────
@app.local_entrypoint()
def main():
    print("Yishun Again — placeholder test generation")
    print(f"  LoRA scale: {LORA_SCALE}  |  steps: {NUM_INFERENCE_STEPS}  |  "
          f"size: {WIDTH}x{HEIGHT}")
    print(f"  Negative prompt: {NEGATIVE_PROMPT}\n")

    tester = PlaceholderTester()

    urls: list[str] = []
    for classification, prompt, r2_key in TEST_CASES:
        url = tester.generate_test.remote(classification, prompt, r2_key)
        urls.append(url)
        print(f"  ✓ {classification}: {url}")

    print("\n" + "=" * 60)
    print("All 3 placeholder test images generated:")
    for (classification, _, _), url in zip(TEST_CASES, urls):
        print(f"  [{classification:<6}] {url}")
    print("=" * 60)
