"""
Yishun Again — standalone LoRA weights uploader
================================================
Recovers from a disconnected training run by reading the final safetensors
from Modal Volume "yishun-training-data" and uploading it to Cloudflare R2.

Usage:
    modal run art/upload_weights.py

Dry-run (print what would happen, no upload):
    modal run art/upload_weights.py --dry-run

Prerequisites:
    pip install modal boto3
    modal token new
    Modal secret "cloudflare-r2" must exist in the Modal dashboard with:
        CF_R2_ACCOUNT_ID, CF_R2_ACCESS_KEY_ID,
        CF_R2_SECRET_ACCESS_KEY, CF_R2_BUCKET_NAME
"""

import os
from pathlib import Path

import modal

VOLUME_WEIGHTS_PATH = "/data/output/yishunagain_v2.safetensors"
R2_LORA_KEY         = "lora/yishunagain_v2.safetensors"
R2_PUBLIC_BASE      = "https://assets.yishunagain.com"

app    = modal.App("yishun-upload-weights")
volume = modal.Volume.from_name("yishun-training-data")

upload_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("boto3")
)


@app.function(
    image=upload_image,
    timeout=600,
    volumes={"/data": volume},
    secrets=[modal.Secret.from_name("cloudflare-r2")],
)
def upload_weights(dry_run: bool = False) -> str:
    """Read weights from Modal Volume and upload to Cloudflare R2."""
    import boto3

    volume.reload()

    weights_path = Path(VOLUME_WEIGHTS_PATH)
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Weights not found at {weights_path} in volume 'yishun-training-data'.\n"
            "Check that training completed and the volume name is correct."
        )

    size_mb = weights_path.stat().st_size / 1e6
    print(f"[upload] Found weights: {weights_path} ({size_mb:.1f} MB)")

    if dry_run:
        print(f"[upload] DRY RUN — would upload to s3://<bucket>/{R2_LORA_KEY}")
        print(f"[upload] Public URL would be: {R2_PUBLIC_BASE}/{R2_LORA_KEY}")
        return f"{R2_PUBLIC_BASE}/{R2_LORA_KEY}"

    account_id = os.environ["CF_R2_ACCOUNT_ID"]
    bucket     = os.environ["CF_R2_BUCKET_NAME"]

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["CF_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CF_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    print(f"[upload] Uploading {size_mb:.1f} MB → s3://{bucket}/{R2_LORA_KEY} …")
    s3.upload_file(str(weights_path), bucket, R2_LORA_KEY)

    url = f"{R2_PUBLIC_BASE}/{R2_LORA_KEY}"
    print(f"[upload] Done: {url}")
    return url


@app.local_entrypoint()
def main(dry_run: bool = False):
    """
    Args:
        dry_run: --dry-run   verify weights exist and print URL without uploading
    """
    if dry_run:
        print("[local] Dry-run mode — no upload will occur.\n")

    url = upload_weights.remote(dry_run=dry_run)

    print()
    print("=" * 60)
    if dry_run:
        print("DRY RUN complete.")
        print(f"  Weights path in volume : {VOLUME_WEIGHTS_PATH}")
        print(f"  Target R2 key          : {R2_LORA_KEY}")
        print(f"  Public URL (after upload): {url}")
    else:
        print("Upload complete.")
        print(f"  R2 URL: {url}")
        print()
        print("Next step — verify the file is accessible:")
        print(f"  curl -I {url}")
    print("=" * 60)
