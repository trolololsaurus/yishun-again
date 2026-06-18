"""
Yishun Again — CivitAI M_Pixel LoRA placeholder test (SD 1.5)
=============================================================
M_Pixel is a **Stable Diffusion 1.5** LoRA. It MUST be loaded onto an SD 1.5
base — loading it onto SDXL leaves the LoRA barely attached and the output
comes out photographic instead of pixel art.

Recipe (author's SD 1.5 spec, https://civitai.com/models/44960/mpixel):
  - base render : 512x768, 30 steps, guidance 7
  - hires fix   : img2img upscale x1.5 -> 768x1152, denoise 0.4, same LoRA
  - trigger word: "pixel" as the FIRST token of every prompt
  - LoRA scale  : 0.6 via cross_attention_kwargs

Generation is decoupled from delivery: each PNG is SHA256-fingerprinted in the
container before upload, and every R2 key carries the run timestamp with a
no-cache header so a URL is never served stale.

Usage:
    modal run art/test_placeholders.py

Prerequisites
-------------
1.  modal token new
2.  Modal secret "cloudflare-r2"
"""

import io
import os
from pathlib import Path

import modal

# ── LoRA config ───────────────────────────────────────────────────────────────
LORA_URL   = "https://civitai.com/api/download/models/52870?fileId=38468"
LORA_LOCAL = "/tmp/mpixel.safetensors"
LORA_SCALE = 0.5  # applied via cross_attention_kwargs={"scale": ...}

# ── Base checkpoint ───────────────────────────────────────────────────────────
# M_Pixel needs a fine-tuned SD1.5 *art* checkpoint with an illustration prior;
# base SD1.5 is photoreal-leaning and renders sprites in a void. CetusMix Coda2
# is one of the proven CivitAI bases. Loaded via from_single_file.
CETUS_REPO     = "casual02/CetusMix_Coda2"
CETUS_FILENAME = "cetusMix_Coda2.safetensors"

# ── General config ────────────────────────────────────────────────────────────
R2_PUBLIC_BASE      = "https://assets.yishunagain.com"
NUM_INFERENCE_STEPS = 30
GUIDANCE_SCALE      = 7.0
CLIP_SKIP           = 2     # critical for CetusMix-style checkpoints
SEED                = 2416205037   # fixed known-good seed from CivitAI example

# Render + hires-fix (latent upscale, replicating A1111 "Hires upscaler: Latent")
WIDTH               = 512
HEIGHT              = 768
HIRES_ENABLED       = True
HIRES_UPSCALE       = 1.5
HIRES_DENOISE       = 0.5

NEGATIVE_PROMPT = (
    "(worst quality, low quality:2), "
    "1girl, female, woman, blonde, blue hair, silver hair, "
    "phone, smartphone, floating objects, "
    "plain background, empty background, white background, grey background"
)

# Three test cases: (classification, prompt, name_stem)
# The final R2 key is built per-run as placeholders/test/{name_stem}-{timestamp}.png
# Structure: quality tags, pixel trigger, fixed character anchor, expression,
# background that fills the frame behind the character.
TEST_CASES: list[tuple[str, str, str]] = [
    (
        "heart",
        "(masterpiece, top quality, best quality), pixel, pixel art, "
        "1man, short black hair, blue collared shirt, dark pants, slim build, "
        "big smile, both hands thumbs up, "
        "HDB void deck background, concrete pillars, potted plants, "
        "warm afternoon light, vibrant colors, full body",
        "good-vibes",
    ),
    (
        "clown",
        "(masterpiece, top quality, best quality), pixel, pixel art, "
        "1man, short black hair, blue collared shirt, dark pants, slim build, "
        "shocked expression, mouth wide open, both hands raised in disbelief, "
        "overturned shopping trolley on ground, cluttered corridor background, "
        "bright daylight, full body",
        "absurdities",
    ),
    (
        "dagger",
        "(masterpiece, top quality, best quality), pixel, pixel art, "
        "1man, short black hair, blue collared shirt, dark pants, slim build, "
        "tense worried expression, furrowed brows, arms crossed, "
        "yellow police tape background, night scene, dim streetlight overhead, "
        "dark blue shadows, full body",
        "dark-events",
    ),
]

# ── Modal resources ───────────────────────────────────────────────────────────
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
        "requests",
        "omegaconf",       # required by from_single_file ckpt conversion
        "pytorch-lightning",
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


# ── Inference class — model loaded once per container ─────────────────────────
@app.cls(
    image=inference_image,
    gpu="A10G",
    timeout=600,
    scaledown_window=120,
    volumes={"/cache": model_cache},
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
class PlaceholderTester:
    @modal.enter()
    def load_model(self):
        import requests
        import torch
        from diffusers import (
            DPMSolverSinglestepScheduler,
            StableDiffusionImg2ImgPipeline,
            StableDiffusionPipeline,
        )
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file

        os.environ["HF_HOME"]            = "/cache/huggingface"
        os.environ["TRANSFORMERS_CACHE"] = "/cache/huggingface"

        # ── Download CivitAI LoRA (force fresh) ──────────────────────────────
        lora_path = Path(LORA_LOCAL)
        if lora_path.exists():
            lora_path.unlink()
            print(f"[load] Removed stale {LORA_LOCAL}")

        print(f"[load] Downloading CivitAI LoRA → {LORA_LOCAL}")
        resp = requests.get(
            LORA_URL,
            stream=True,
            timeout=300,
            headers={"User-Agent": "yishun-again/1.0"},
            allow_redirects=True,
        )
        resp.raise_for_status()
        with open(LORA_LOCAL, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MB chunks
                fh.write(chunk)
        size_mb = lora_path.stat().st_size / 1e6
        print(f"[load] LoRA downloaded ({size_mb:.1f} MB)")

        # ── Download + load CetusMix Coda2 base checkpoint (single file) ──────
        print(f"[load] Downloading base checkpoint {CETUS_REPO}/{CETUS_FILENAME}…")
        ckpt_path = hf_hub_download(repo_id=CETUS_REPO, filename=CETUS_FILENAME)
        print(f"[load] Loading CetusMix Coda2 via from_single_file…")
        self.pipe = StableDiffusionPipeline.from_single_file(
            ckpt_path,
            torch_dtype=torch.float16,
            load_safety_checker=False,
        )

        # ── Sampler: DPM++ SDE Karras ────────────────────────────────────────
        self.pipe.scheduler = DPMSolverSinglestepScheduler.from_config(
            self.pipe.scheduler.config, use_karras_sigmas=True
        )
        print("[load] Scheduler → DPMSolverSinglestepScheduler (Karras sigmas)")

        # ── Load LoRA the standard way + verify it actually attaches ─────────
        raw_keys = len(load_file(LORA_LOCAL))
        unet_before = self._count_lora_in_unet()
        self.pipe.load_lora_weights(LORA_LOCAL)
        unet_after = self._count_lora_in_unet()
        attached = unet_after - unet_before
        print(
            f"[lora] M_Pixel loaded: {raw_keys} tensors in file → "
            f"{attached} LoRA layers attached to UNet "
            f"(scale {LORA_SCALE} via cross_attention_kwargs)"
        )
        if attached == 0:
            raise RuntimeError(
                "No LoRA keys matched the UNet — architecture mismatch! "
                "M_Pixel is SD 1.5; ensure the base is an SD 1.5 checkpoint."
            )

        self.pipe.enable_xformers_memory_efficient_attention()
        self.pipe = self.pipe.to("cuda")

        # img2img shares the SAME components → the loaded LoRA + scheduler carry
        # over to the hires-fix pass (cross_attention scale passed on each call).
        self.img2img = StableDiffusionImg2ImgPipeline(**self.pipe.components)
        self.img2img.set_progress_bar_config(disable=False)
        print("[load] Pipeline ready (CetusMix Coda2 + M_Pixel + latent hires).")

    def _count_lora_in_unet(self) -> int:
        """Count LoRA-bearing attention processors currently on the UNet."""
        try:
            procs = self.pipe.unet.attn_processors
        except Exception:
            return 0
        return sum(1 for p in procs.values() if "lora" in type(p).__name__.lower())

    @modal.method()
    def generate_test(
        self, classification: str, prompt: str, name_stem: str, timestamp: int
    ) -> dict:
        """Generate one image, hash it, upload to a unique timestamped R2 key.

        Returns {url, sha256, size, name_stem} so the caller can print a summary.
        """
        import hashlib
        import torch
        import torch.nn.functional as F

        print(f"\n[gen:{classification}] Prompt: {prompt}")

        # Fixed seed → reproducible across all 3 classes and across runs
        generator = torch.Generator(device="cuda").manual_seed(SEED)

        # ── Stage 1: base render at 512x768 → return LATENTS ─────────────────
        with torch.inference_mode():
            base_latents = self.pipe(
                prompt=prompt,
                negative_prompt=NEGATIVE_PROMPT,
                num_inference_steps=NUM_INFERENCE_STEPS,
                guidance_scale=GUIDANCE_SCALE,
                width=WIDTH,
                height=HEIGHT,
                clip_skip=CLIP_SKIP,
                generator=generator,
                cross_attention_kwargs={"scale": LORA_SCALE},
                output_type="latent",
            ).images  # tensor (1, 4, H/8, W/8)

        if HIRES_ENABLED:
            # ── Stage 2: LATENT upscale x1.5, decode, img2img @ denoise 0.5 ──
            up = F.interpolate(
                base_latents, scale_factor=HIRES_UPSCALE,
                mode="bilinear", align_corners=False,
            )
            hires_w = int(WIDTH * HIRES_UPSCALE)
            hires_h = int(HEIGHT * HIRES_UPSCALE)
            print(f"[gen:{classification}] Latent hires → {hires_w}x{hires_h} (denoise {HIRES_DENOISE})")

            # Decode upscaled latents to an init image for img2img
            with torch.inference_mode():
                vae = self.pipe.vae
                dec = vae.decode(up.to(vae.dtype) / vae.config.scaling_factor).sample
            dec = (dec / 2 + 0.5).clamp(0, 1)[0].permute(1, 2, 0).float().cpu().numpy()
            from PIL import Image as _Image
            init_img = _Image.fromarray((dec * 255).round().astype("uint8"))

            with torch.inference_mode():
                image = self.img2img(
                    prompt=prompt,
                    negative_prompt=NEGATIVE_PROMPT,
                    image=init_img,
                    strength=HIRES_DENOISE,
                    num_inference_steps=NUM_INFERENCE_STEPS,
                    guidance_scale=GUIDANCE_SCALE,
                    clip_skip=CLIP_SKIP,
                    generator=torch.Generator(device="cuda").manual_seed(SEED),
                    cross_attention_kwargs={"scale": LORA_SCALE},
                ).images[0]
        else:
            # Decode base latents directly (no hires)
            with torch.inference_mode():
                vae = self.pipe.vae
                dec = vae.decode(base_latents.to(vae.dtype) / vae.config.scaling_factor).sample
            dec = (dec / 2 + 0.5).clamp(0, 1)[0].permute(1, 2, 0).float().cpu().numpy()
            from PIL import Image as _Image
            image = _Image.fromarray((dec * 255).round().astype("uint8"))

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # ── Fingerprint the exact bytes BEFORE upload — proves gen != delivery ──
        sha256 = hashlib.sha256(png_bytes).hexdigest()
        size   = len(png_bytes)
        print(f"[GEN] {name_stem}  sha256={sha256}  size={size} bytes")

        # ── Unique, never-cached key per run ──────────────────────────────────
        r2_key = f"placeholders/test/{name_stem}-{timestamp}.png"
        bucket = os.environ["CF_R2_BUCKET_NAME"]
        print(f"[gen:{classification}] Uploading → R2: {r2_key}")
        _r2_client().put_object(
            Bucket=bucket,
            Key=r2_key,
            Body=png_bytes,
            ContentType="image/png",
            CacheControl="no-cache, max-age=0",
        )

        url = f"{R2_PUBLIC_BASE}/{r2_key}"
        print(f"[gen:{classification}] Done: {url}")
        return {"url": url, "sha256": sha256, "size": size, "name_stem": name_stem}


# ── Local entrypoint — runs all 3 sequentially ───────────────────────────────
@app.local_entrypoint()
def main():
    import time

    timestamp = int(time.time())  # one shared run id → unique keys, never cached

    hires = (f"latent x{HIRES_UPSCALE} @ denoise {HIRES_DENOISE}"
             if HIRES_ENABLED else "OFF")
    print("Yishun Again — CivitAI M_Pixel LoRA test (CetusMix Coda2)")
    print(f"  LoRA      : {LORA_URL}")
    print(f"  Base model: {CETUS_REPO}/{CETUS_FILENAME}")
    print(f"  Sampler   : DPM++ SDE Karras  |  clip_skip: {CLIP_SKIP}")
    print(f"  LoRA scale: {LORA_SCALE}  |  Steps: {NUM_INFERENCE_STEPS}  |  CFG: {GUIDANCE_SCALE}")
    print(f"  Render    : {WIDTH}x{HEIGHT}  |  hires fix: {hires}")
    print(f"  Seed      : {SEED} (fixed for all 3)")
    print(f"  Trigger   : pixel")
    print(f"  Timestamp : {timestamp}")
    print(f"  Negative  : {NEGATIVE_PROMPT}\n")

    tester = PlaceholderTester()

    results: list[dict] = []
    for classification, prompt, name_stem in TEST_CASES:
        res = tester.generate_test.remote(classification, prompt, name_stem, timestamp)
        results.append(res)
        print(f"  ✓ {classification}: {res['url']}")

    print("\n" + "=" * 70)
    print("All 3 placeholder test images generated:")
    print("-" * 70)
    for (classification, _, _), res in zip(TEST_CASES, results):
        print(f"  [{classification:<6}] sha256={res['sha256']}")
        print(f"            size  ={res['size']} bytes")
        print(f"            url   ={res['url']}")
    print("=" * 70)
    print("\nUnique URLs:")
    for res in results:
        print(f"  {res['url']}")
