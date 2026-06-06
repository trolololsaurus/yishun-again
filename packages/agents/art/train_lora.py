"""
Yishun Again — SDXL LoRA training pipeline on Modal.run
=========================================================
Run:  modal run packages/agents/art/train_lora.py

Prerequisites
-------------
1.  pip install modal Pillow boto3
2.  modal token new                    (authenticate once)
3.  Create Modal secret "cloudflare-r2" in the Modal dashboard with:
        CF_R2_ACCOUNT_ID
        CF_R2_ACCESS_KEY_ID
        CF_R2_SECRET_ACCESS_KEY
        CF_R2_BUCKET_NAME              (value: yishun-assets)
4.  Training images must exist at LOCAL_TRAIN_DIR (Windows path ok)

Pipeline
--------
1.  [local]  Resize images < 512px shortest side with Pillow
             Upload to Modal Volume "yishun-training-data"
2.  [GPU A10G]  BLIP2 auto-caption every image
                Prefix: "yishunpixel, 16-bit pixel art,"
3.  [GPU A10G]  kohya_ss SDXL LoRA training (1500 steps, fp16)
4.  [Modal]   Upload final .safetensors → Cloudflare R2
              Print R2 public URL
"""

import io
import os
import subprocess
import sys
from pathlib import Path

import modal

# ── Training config ──────────────────────────────────────────────────────────
LOCAL_TRAIN_DIR    = r"C:\Projects\yishun-again\packages\agents\art\training_images"
TRIGGER_WORD       = "yishunpixel"
CAPTION_PREFIX     = "yishunpixel, HD-2D pixel art, Octopath Traveler style,"
BASE_MODEL         = "stabilityai/stable-diffusion-xl-base-1.0"
LORA_NAME          = "yishunagain_v2"
R2_LORA_KEY        = "lora/yishunagain_v2.safetensors"
R2_PUBLIC_BASE     = "https://assets.yishunagain.com"
MIN_EDGE           = 512
NUM_REPEATS        = 10

MAX_TRAIN_STEPS    = 2000
TRAIN_BATCH_SIZE   = 1
LEARNING_RATE      = "1e-4"
NETWORK_DIM        = 32
NETWORK_ALPHA      = 16
LR_SCHEDULER       = "cosine_with_restarts"
MIXED_PRECISION    = "fp16"
SAVE_EVERY_N_STEPS = 500

# ── Modal resources ──────────────────────────────────────────────────────────
app      = modal.App("yishun-lora-trainer")
volume   = modal.Volume.from_name("yishun-training-data", create_if_missing=True)
hf_cache = modal.Volume.from_name("yishun-hf-cache",      create_if_missing=True)

VOLUME_TRAIN_DIR  = f"/data/train/{NUM_REPEATS}_{TRIGGER_WORD}"
VOLUME_OUTPUT_DIR = "/data/output"

# ── Training image — minimal kohya_ss stack, no BLIP2 ────────────────────────
# Uses the original compatible versions: hf_hub 0.21.3 has cached_download,
# transformers 4.36.2 + tokenizers 0.15.2 are what kohya_ss expects.
# BLIP2 captioning uses a separate image (caption_image below).
training_image = (
    modal.Image.from_registry(
        "nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install(
        "git", "wget", "libgl1-mesa-glx", "libglib2.0-0", "build-essential",
    )
    .pip_install(
        "torch==2.1.0+cu118",
        "torchvision==0.16.0+cu118",
        extra_index_url="https://download.pytorch.org/whl/cu118",
    )
    .pip_install(
        "xformers==0.0.23.post1+cu118",
        extra_index_url="https://download.pytorch.org/whl/cu118",
    )
    .pip_install(
        "numpy==1.26.4",
        "huggingface_hub==0.21.3",
        "tokenizers==0.15.2",
        "transformers==4.36.2",
        "accelerate==0.26.1",
        "diffusers==0.25.0",
        "safetensors==0.4.1",
        "bitsandbytes==0.41.3",
        "lion-pytorch",
        "prodigyopt",
        "einops",
        "toml",
        "Pillow>=10.0.0",
        "voluptuous",
        "sentencepiece",
        "opencv-python-headless",
        "imagesize",
        "ftfy",
        "tensorboard",
        "boto3",
    )
    .run_commands(
        "git clone --depth=1 https://github.com/kohya-ss/sd-scripts /kohya_ss",
        # Install kohya's remaining deps (skip the broken local file:// reference)
        "grep -v 'file://' /kohya_ss/requirements.txt > /tmp/kohya_req.txt"
        " && pip install -q --no-deps -r /tmp/kohya_req.txt || true",
    )
)

# ── Caption image — BLIP2 stack with newer transformers/tokenizers ────────────
# Kept separate because transformers>=4.40 requires huggingface_hub>=0.23
# which conflicts with diffusers 0.25's cached_download dependency.
caption_image = (
    modal.Image.from_registry(
        "nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install("libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "torch==2.1.0+cu118",
        extra_index_url="https://download.pytorch.org/whl/cu118",
    )
    .pip_install(
        "numpy==1.26.4",
        "tokenizers==0.19.1",
        "transformers==4.44.2",
        "accelerate==0.26.1",
        "safetensors==0.4.1",
        "Pillow>=10.0.0",
        "sentencepiece",
    )
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


# ── Step 1b: Write pre-processed images into the Modal Volume ────────────────
# volume.put_file() requires Modal ≥0.64. For 0.62 we pass bytes through a
# regular function call so the write happens inside a volume-mounted container.
@app.function(
    image=training_image,
    timeout=300,
    volumes={"/data": volume},
)
def write_images_to_volume(images: dict) -> int:
    """Receive {filename: bytes} dict, write to training dir, commit volume."""
    train_dir = Path(VOLUME_TRAIN_DIR)
    train_dir.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        (train_dir / name).write_bytes(data)
    volume.commit()
    return len(images)


# ── Step 2: BLIP2 captioning (A10G GPU) — uses separate caption_image ────────
@app.function(
    image=caption_image,
    gpu="A10G",
    timeout=1800,
    volumes={
        "/data": volume,
        "/root/.cache/huggingface": hf_cache,
    },
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
def caption_images() -> int:
    """Auto-caption training images with BLIP2. Returns number captioned."""
    import torch
    from PIL import Image
    from transformers import Blip2ForConditionalGeneration, Blip2Processor

    volume.reload()
    train_dir  = Path(VOLUME_TRAIN_DIR)
    img_files  = sorted(f for f in train_dir.iterdir() if f.suffix.lower() in IMAGE_EXTS)
    print(f"[caption] {len(img_files)} images found in {train_dir}")

    processor = Blip2Processor.from_pretrained("Salesforce/blip2-opt-2.7b")
    model = Blip2ForConditionalGeneration.from_pretrained(
        "Salesforce/blip2-opt-2.7b",
        torch_dtype=torch.float16,
        device_map="auto",
    )
    model.eval()

    captioned = 0
    for img_path in img_files:
        txt_path = img_path.with_suffix(".txt")
        if txt_path.exists():
            print(f"[caption] skip {img_path.name} (already captioned)")
            continue

        with Image.open(img_path).convert("RGB") as img:
            inputs = processor(images=img, return_tensors="pt").to("cuda", torch.float16)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=80)
        blip_text = processor.decode(out[0], skip_special_tokens=True).strip()

        caption = f"{CAPTION_PREFIX} {blip_text}"
        txt_path.write_text(caption, encoding="utf-8")
        print(f"[caption] {img_path.name}: {caption}")
        captioned += 1

    volume.commit()
    print(f"[caption] Done — {captioned} new captions written.")
    return captioned


# ── Step 3: kohya_ss SDXL LoRA training (A10G GPU) ──────────────────────────
@app.function(
    image=training_image,
    gpu="A10G",
    timeout=7200,
    volumes={
        "/data": volume,
        "/root/.cache/huggingface": hf_cache,
    },
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
def run_training() -> str:
    """Run kohya_ss SDXL LoRA training. Returns path to final safetensors."""
    import toml

    volume.reload()

    output_dir = Path(VOLUME_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Accelerate config — single GPU, fp16, no distributed
    accel_dir = Path.home() / ".cache" / "huggingface" / "accelerate"
    accel_dir.mkdir(parents=True, exist_ok=True)
    (accel_dir / "default_config.yaml").write_text(
        "compute_environment: LOCAL_MACHINE\n"
        "distributed_type: 'NO'\n"
        "downcast_bf16: 'no'\n"
        "gpu_ids: all\n"
        "machine_rank: 0\n"
        "main_training_function: main\n"
        f"mixed_precision: {MIXED_PRECISION}\n"
        "num_machines: 1\n"
        "num_processes: 1\n"
        "rdzv_backend: static\n"
        "same_network: true\n"
        "tpu_env: []\n"
        "tpu_use_sudo: false\n"
        "use_cpu: false\n",
        encoding="utf-8",
    )

    # kohya_ss dataset config TOML
    dataset_cfg = {
        "general": {
            "shuffle_caption": True,
            "caption_extension": ".txt",
            "keep_tokens": 1,
        },
        "datasets": [
            {
                "resolution": 512,
                "batch_size": TRAIN_BATCH_SIZE,
                "enable_bucket": True,
                "min_bucket_reso": 256,
                "max_bucket_reso": 1024,
                "subsets": [
                    {
                        "image_dir": VOLUME_TRAIN_DIR,
                        "caption_extension": ".txt",
                        "num_repeats": NUM_REPEATS,
                    }
                ],
            }
        ],
    }
    toml_path = Path("/data/dataset.toml")
    toml_path.write_text(toml.dumps(dataset_cfg), encoding="utf-8")
    print(f"[train] Dataset config → {toml_path}")

    cmd = [
        "accelerate", "launch",
        "--num_cpu_threads_per_process", "4",
        "/kohya_ss/sdxl_train_network.py",
        f"--pretrained_model_name_or_path={BASE_MODEL}",
        f"--dataset_config={toml_path}",
        f"--output_dir={VOLUME_OUTPUT_DIR}",
        f"--output_name={LORA_NAME}",
        "--save_model_as=safetensors",
        "--network_module=networks.lora",
        f"--network_dim={NETWORK_DIM}",
        f"--network_alpha={NETWORK_ALPHA}",
        f"--learning_rate={LEARNING_RATE}",
        f"--lr_scheduler={LR_SCHEDULER}",
        "--lr_warmup_steps=100",
        f"--max_train_steps={MAX_TRAIN_STEPS}",
        f"--train_batch_size={TRAIN_BATCH_SIZE}",
        f"--mixed_precision={MIXED_PRECISION}",
        f"--save_every_n_steps={SAVE_EVERY_N_STEPS}",
        "--xformers",
        "--cache_latents",
        "--cache_latents_to_disk",
        "--gradient_checkpointing",
        f"--logging_dir={VOLUME_OUTPUT_DIR}/logs",
    ]

    print(f"[train] Starting: {MAX_TRAIN_STEPS} steps, lr={LEARNING_RATE}, "
          f"dim={NETWORK_DIM}, alpha={NETWORK_ALPHA}")
    subprocess.run(cmd, cwd="/kohya_ss", check=True)

    final_path = output_dir / f"{LORA_NAME}.safetensors"
    if not final_path.exists():
        raise FileNotFoundError(
            f"Training finished but weights not found at {final_path}. "
            "Check training logs in VOLUME_OUTPUT_DIR/logs."
        )

    print(f"[train] Complete — {final_path} ({final_path.stat().st_size / 1e6:.1f} MB)")
    volume.commit()
    return str(final_path)


# ── Step 4: Upload to Cloudflare R2 ─────────────────────────────────────────
@app.function(
    image=training_image,
    timeout=300,
    volumes={"/data": volume},
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
def upload_weights_to_r2() -> str:
    """Upload final LoRA weights to R2. Returns public URL."""
    import boto3

    volume.reload()

    weights_path = Path(VOLUME_OUTPUT_DIR) / f"{LORA_NAME}.safetensors"
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Weights not found at {weights_path}. Run training first."
        )

    account_id = os.environ["CF_R2_ACCOUNT_ID"]
    bucket     = os.environ["CF_R2_BUCKET_NAME"]

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    size_mb = weights_path.stat().st_size / 1e6
    print(f"[r2] Uploading {size_mb:.1f} MB → s3://{bucket}/{R2_LORA_KEY}")
    s3.upload_file(str(weights_path), bucket, R2_LORA_KEY)

    url = f"{R2_PUBLIC_BASE}/{R2_LORA_KEY}"
    print(f"[r2] Done: {url}")
    return url


# ── Local entrypoint — orchestrates all 4 steps ──────────────────────────────
@app.local_entrypoint()
def main(skip_upload: bool = False, skip_caption: bool = True):
    """
    Args:
        skip_upload:  --skip-upload   skip image upload (images already in volume)
        skip_caption: --skip-caption  skip BLIP2 captioning (captions already written)
    """
    from PIL import Image  # must be installed locally: pip install Pillow

    # ── Step 1: resize locally → collect bytes → write to Modal Volume ──────
    if skip_upload:
        print("[step 1] Skipping image upload (--skip-upload set)")
    else:
        local_dir = Path(LOCAL_TRAIN_DIR)
        if not local_dir.exists():
            print(f"ERROR: training dir not found: {local_dir}", file=sys.stderr)
            sys.exit(1)

        all_images = [p for p in local_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        if not all_images:
            print(f"ERROR: no images in {local_dir}", file=sys.stderr)
            sys.exit(1)

        print(f"\n[step 1] Resizing {len(all_images)} images and uploading to Modal Volume…")
        images_payload: dict[str, bytes] = {}
        for img_path in sorted(all_images):
            with Image.open(img_path) as img:
                w, h = img.size
                if min(w, h) < MIN_EDGE:
                    scale = MIN_EDGE / min(w, h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=95)
            images_payload[f"{img_path.stem}.jpg"] = buf.getvalue()
            print(f"  queued {img_path.name} ({len(buf.getvalue()) // 1024} KB)")
            # Also bundle the pre-written .txt caption if present
            txt_path = img_path.with_suffix(".txt")
            if txt_path.exists():
                images_payload[f"{img_path.stem}.txt"] = txt_path.read_bytes()

        n_imgs = sum(1 for k in images_payload if not k.endswith(".txt"))
        n_caps = sum(1 for k in images_payload if k.endswith(".txt"))
        n = write_images_to_volume.remote(images_payload)
        print(f"[step 1] {n_imgs} images + {n_caps} captions written to volume 'yishun-training-data'")

    # ── Step 2: BLIP2 captioning ─────────────────────────────────────────
    if skip_caption:
        print("[step 2] Skipping BLIP2 captioning (--skip-caption set)")
    else:
        print("\n[step 2] Running BLIP2 captioning (A10G GPU)…")
        n_captioned = caption_images.remote()
        print(f"[step 2] {n_captioned} captions written")

    # ── Step 3: LoRA training ─────────────────────────────────────────────
    print("\n[step 3] Running kohya_ss training (A10G GPU, ~30–60 min)…")
    weights_path = run_training.remote()
    print(f"[step 3] Weights saved to volume: {weights_path}")

    # ── Step 4: Upload to R2 ──────────────────────────────────────────────
    print("\n[step 4] Uploading to Cloudflare R2…")
    r2_url = upload_weights_to_r2.remote()

    print(f"\n✓ Pipeline complete")
    print(f"  LoRA weights: {r2_url}")
    print(f"  Trigger word: {TRIGGER_WORD}")
    print(f"  Use in generate_pixel_art.py with LORA_R2_KEY = '{R2_LORA_KEY}'")
