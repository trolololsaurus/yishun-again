"""
Yishun Again — SDXL pixel art generation via Modal.run
=======================================================
Usage (test a single incident):
    modal run packages/agents/art/generate_pixel_art.py \\
        --title "Man argues with void deck chair" \\
        --classification clown \\
        --area-name "Yishun Avenue 11" \\
        --slug "man-arguing-void-deck-chair-block-412-yishun"

    Output: /tmp/test_output.png  +  R2 URL printed to stdout

Called from the agent pipeline (after War Room approval):
    from packages.agents.art.generate_pixel_art import PixelArtGenerator
    gen = PixelArtGenerator()
    url = gen.generate.remote(title, classification, area_name, slug)

Prerequisites
-------------
1.  modal token new
2.  Modal secret "cloudflare-r2"
"""

import io
import os
from pathlib import Path

import modal

# ── Config ────────────────────────────────────────────────────────────────────
R2_ART_PREFIX  = "pixel-art"
R2_PUBLIC_BASE = "https://assets.yishunagain.com"
BASE_MODEL     = "stabilityai/stable-diffusion-xl-base-1.0"

NEGATIVE_PROMPT = (
    "photorealistic, 3d render, photograph, blurry, people faces, "
    "text, watermark, low quality, deformed, ugly, out of frame"
)

# Mood guidance per classification — woven into the prompt
CLASSIFICATION_MOOD: dict[str, str] = {
    "heart":  "warm amber lighting, community gathering, cheerful atmosphere, hopeful tones",
    "clown":  "chaotic void deck, bright garish colours, absurd props, comedic mayhem",
    "dagger": "dark night scene, harsh shadows, police tape, deep red and blue tones, ominous",
    "custom": "dramatic Yishun HDB environment, cinematic lighting",
}

# ── Modal resources ───────────────────────────────────────────────────────────
app         = modal.App("yishun-pixel-art-generator")
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


def _build_prompt(title: str, classification: str, area_name: str | None) -> str:
    location = area_name or "Yishun"
    mood     = CLASSIFICATION_MOOD.get(classification, CLASSIFICATION_MOOD["custom"])
    return (
        f"HD-2D pixel art, HDB void deck Singapore, {location}, "
        f"{mood}, isometric view, JRPG style, "
        f"detailed pixel art scene, masterpiece"
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
class PixelArtGenerator:
    @modal.enter()
    def load_model(self):
        import torch
        from diffusers import StableDiffusionXLPipeline

        # HF downloads go to the cached volume — only happens on first container start
        os.environ["HF_HOME"]            = "/cache/huggingface"
        os.environ["TRANSFORMERS_CACHE"] = "/cache/huggingface"

        print(f"[load] Loading SDXL pipeline from {BASE_MODEL}…")
        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            BASE_MODEL,
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
        )

        # LoRA: replaced with CivitAI model — see PROMPT 2

        self.pipe.enable_xformers_memory_efficient_attention()
        self.pipe = self.pipe.to("cuda")
        print("[load] Pipeline ready.")

    @modal.method()
    def generate(
        self,
        title: str,
        classification: str,
        area_name: str | None,
        slug: str,
        num_inference_steps: int = 30,
        guidance_scale: float = 7.5,
    ) -> str:
        """
        Generate pixel art for an incident and upload to R2.
        Returns the public R2 URL.
        """
        import torch

        prompt = _build_prompt(title, classification, area_name)
        print(f"[gen] Prompt: {prompt}")

        with torch.inference_mode():
            result = self.pipe(
                prompt=prompt,
                negative_prompt=NEGATIVE_PROMPT,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                width=1024,
                height=1024,
            )

        image = result.images[0]
        # Resize to 1200x630 for OG share card compatibility
        image = image.resize((1200, 630), resample=0)  # NEAREST for pixel art

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        r2_key = f"{R2_ART_PREFIX}/{slug}.png"
        bucket = os.environ["CF_R2_BUCKET_NAME"]
        print(f"[gen] Uploading to R2: {r2_key}")
        _r2_client().put_object(
            Bucket=bucket,
            Key=r2_key,
            Body=buf.getvalue(),
            ContentType="image/png",
        )

        url = f"{R2_PUBLIC_BASE}/{r2_key}"
        print(f"[gen] Done: {url}")
        return url


# ── Local entrypoint — for testing a single incident ─────────────────────────
@app.local_entrypoint()
def main(
    title: str = "Man found arguing with void deck chair at Block 412 Yishun Ave 11",
    classification: str = "clown",
    area_name: str = "Yishun Avenue 11",
    slug: str = "man-arguing-void-deck-chair-block-412-yishun",
):
    from PIL import Image as PILImage

    prompt = _build_prompt(title, classification, area_name)
    print(f"\nPrompt preview:\n  {prompt}")
    print(f"Negative: {NEGATIVE_PROMPT}\n")

    generator = PixelArtGenerator()
    r2_url = generator.generate.remote(title, classification, area_name, slug)

    # Download generated image locally for inspection
    import tempfile
    import urllib.request
    local_out = Path(tempfile.gettempdir()) / "test_output.png"
    try:
        urllib.request.urlretrieve(r2_url, local_out)
        img = PILImage.open(local_out)
        print(f"\n[done] Saved to {local_out}  ({img.size[0]}x{img.size[1]} px)")
    except Exception as exc:
        print(f"  (Could not download preview locally: {exc})")

    print(f"[done] R2 URL:  {r2_url}")
    print(f"  Use this as pixel_art_url in the incidents table.")
