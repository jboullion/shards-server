"""
Image models for the restyle step. Each provider takes (images, prompt, model) and returns
(PIL image, info). The first image is the icon to redraw; any others are style references.
Standard library HTTP only (urllib), so the system Python with Pillow + numpy is enough.

    echo     no model: returns the input. Free; for trying the pipeline end to end.
    comfy    ComfyUI (COMFY_URL, default 127.0.0.1:8188): SDXL img2img with an optional LoRA. No style
             references (img2img only sees the icon); the prompt carries the style.
    qwen     Qwen-Image-Edit in a ComfyUI with a big GPU (QWEN_COMFY_URL, e.g. a RunPod pod). Takes
             the style references, as one sheet.
    openai   GPT Image edits (/v1/images/edits), OPENAI_API_KEY. Takes the style references.
    fal      any fal.ai image-edit model taking prompt + image_url(s) (Flux Kontext,
             Qwen-Image-Edit, ...), FAL_AI_API_KEY.

A provider spec is "provider" or "provider@model", e.g. "fal@fal-ai/qwen-image-edit" or
"comfy@waiIllustriousSDXL_v160.safetensors". The OpenAI and fal clients follow
meridian-unreal/tools/aigen/restyle.py.
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image

import env

DEFAULT_MODELS = {"echo": "none", "comfy": "sd_xl_base_1.0_0.9vae.safetensors",
                  "qwen": "qwen_image_edit_2511_fp8mixed.safetensors",
                  "openai": "gpt-image-2.5-sunburst", "fal": "fal-ai/flux-pro/kontext"}
COMFY_URL = "http://127.0.0.1:8188"
TIMEOUT = 600


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# Cloudflare (in front of RunPod's proxy) answers Python's default User-Agent with error 1010.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) iconforge/1.0"


def _transient(code: int, body: str) -> bool:
    """Busy or throttled, not refused: worth retrying. Out of credits is not transient."""
    if "insufficient_quota" in body or "credit_balance" in body:
        return False
    return code in (502, 503, 504) or (code == 429 and ("throttled" in body or "rate_limit" in body))


def _request(url, data=None, headers=None, method=None, timeout=TIMEOUT, tries=5) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:800].decode("utf-8", "replace")
            if attempt + 1 < tries and _transient(e.code, body):
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            # error bodies don't echo keys; keep them short
            raise RuntimeError("%s: HTTP %d %s" % (url.split("?")[0], e.code, body))


def _json(url, body=None, headers=None, method=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers or {})
    if data is not None:
        h["Content-Type"] = "application/json"
    return json.loads(_request(url, data, h, method))


def _multipart(fields: dict, files: list) -> tuple:
    """fields {name: value}, files [(name, filename, bytes, mime)] -> (body, content type)."""
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    for name, filename, data, mime in files:
        out.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                   "Content-Type: %s\r\n\r\n" % (boundary, name, filename, mime)).encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(("--%s--\r\n" % boundary).encode())
    return out.getvalue(), "multipart/form-data; boundary=" + boundary


def echo(images, text, model, opts):
    return images[0].copy(), {}


def openai(images, text, model, opts):
    key = env.require("OPENAI_API_KEY")
    fields = {"model": model, "prompt": text, "size": "1024x1024", "quality": opts.get("quality", "high"),
              "output_format": "png", "n": "1"}
    files = [("image[]", "ref%d.png" % i, _png(im), "image/png") for i, im in enumerate(images)]
    body, ctype = _multipart(fields, files)
    out = json.loads(_request("https://api.openai.com/v1/images/edits", body,
                              {"Authorization": "Bearer " + key, "Content-Type": ctype}))
    return Image.open(io.BytesIO(base64.b64decode(out["data"][0]["b64_json"]))), {"usage": out.get("usage")}


# --- OpenAI Batch API: the same /v1/images/edits requests at half price, answered within 24 hours.
# (Follows meridian-unreal/tools/aigen/restyle.py: JSON bodies with the images as data URLs.)

def openai_batch_line(custom_id: str, images: list, text: str, model: str, opts: dict) -> dict:
    return {"custom_id": custom_id, "method": "POST", "url": "/v1/images/edits",
            "body": {"model": model, "prompt": text, "size": "1024x1024", "quality": opts.get("quality", "high"),
                     "output_format": "png", "n": 1,
                     "images": [{"image_url": "data:image/png;base64," + base64.b64encode(_png(im)).decode()}
                                for im in images]}}


def _openai_auth() -> dict:
    return {"Authorization": "Bearer " + env.require("OPENAI_API_KEY")}


def openai_batch_create(lines: list) -> dict:
    """Upload the requests as JSONL and start a batch -> the batch object (id, status, ...)."""
    body = "\n".join(json.dumps(line) for line in lines).encode()
    form, ctype = _multipart({"purpose": "batch"}, [("file", "iconforge_batch.jsonl", body, "application/jsonl")])
    up = json.loads(_request("https://api.openai.com/v1/files", form, {**_openai_auth(), "Content-Type": ctype}))
    return _json("https://api.openai.com/v1/batches",
                 {"input_file_id": up["id"], "endpoint": "/v1/images/edits", "completion_window": "24h"},
                 _openai_auth())


def openai_batch_get(batch_id: str) -> dict:
    return _json("https://api.openai.com/v1/batches/" + batch_id, headers=_openai_auth())


def openai_batch_results(batch: dict) -> dict:
    """custom_id -> {"image", "usage"} or {"error"} for a finished batch."""
    out = {}
    for fid in (batch.get("output_file_id"), batch.get("error_file_id")):
        if not fid:
            continue
        text = _request("https://api.openai.com/v1/files/%s/content" % fid, headers=_openai_auth()).decode()
        for line in text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            resp = row.get("response") or {}
            body = resp.get("body") or {}
            if row.get("error") or resp.get("status_code", 200) >= 400 or not body.get("data"):
                out[row["custom_id"]] = {"error": row.get("error") or body.get("error") or resp.get("status_code")}
            else:
                out[row["custom_id"]] = {"image": Image.open(io.BytesIO(base64.b64decode(body["data"][0]["b64_json"]))),
                                         "usage": body.get("usage")}
    return out


def fal(images, text, model, opts):
    key = env.require("FAL_AI_API_KEY")
    uris = ["data:image/png;base64," + base64.b64encode(_png(im)).decode() for im in images]
    body = {"prompt": text, "image_url": uris[0], "output_format": "png", "num_images": 1}
    if len(uris) > 1:
        body["image_urls"] = uris
    out = _json("https://fal.run/" + model, body, {"Authorization": "Key " + key})
    img = _request(out["images"][0]["url"], timeout=120)
    return Image.open(io.BytesIO(img)), {"seed": out.get("seed")}


def _comfy_url(env_name: str) -> str:
    return (env.get(env_name) or COMFY_URL).rstrip("/")


def _comfy_upload(base: str, img: Image.Image) -> str:
    body, ctype = _multipart({"overwrite": "true"},
                             [("image", "iconforge_%s.png" % uuid.uuid4().hex[:8], _png(img.convert("RGB")), "image/png")])
    return json.loads(_request(base + "/upload/image", body, {"Content-Type": ctype}))["name"]


def _comfy_run(base: str, graph: dict, info: dict):
    """Queue an API-format graph, wait for its first saved image -> (image, info)."""
    pid = _json(base + "/prompt", {"prompt": graph, "client_id": "iconforge"})["prompt_id"]
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        hist = _json(base + "/history/" + pid)
        if pid in hist:
            entry = hist[pid]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError("comfy: %s" % json.dumps(entry["status"])[:800])
            for node in entry.get("outputs", {}).values():
                for im in node.get("images", []):
                    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im["subfolder"], "type": im["type"]})
                    return Image.open(io.BytesIO(_request(base + "/view?" + q))), info
        time.sleep(1.5)
    raise RuntimeError("comfy: no result within %ds" % TIMEOUT)


def comfy(images, text, model, opts):
    """SDXL img2img in ComfyUI (COMFY_URL, default the local one). opts: denoise (0.55), steps (28),
    cfg (6), seed, lora (file name in models/loras), lora_strength (0.9), negative."""
    base = _comfy_url("COMFY_URL")
    name = _comfy_upload(base, images[0].convert("RGB").resize((1024, 1024), Image.LANCZOS))
    neg = opts.get("negative", "photo, photorealistic, 3d render, blurry, soft shading, gradient background, text, watermark")
    seed = int(opts.get("seed", int(time.time()) % 2**31))
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "2": {"class_type": "LoadImage", "inputs": {"image": name}},
        "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["1", 1]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["1", 1]}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                                                    "latent_image": ["3", 0], "seed": seed,
                                                    "steps": int(opts.get("steps", 28)), "cfg": float(opts.get("cfg", 6)),
                                                    "sampler_name": "dpmpp_2m", "scheduler": "karras",
                                                    "denoise": float(opts.get("denoise", 0.55))}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "iconforge"}},
    }
    if opts.get("lora"):
        graph["9"] = {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": opts["lora"],
                                                             "strength_model": float(opts.get("lora_strength", 0.9)),
                                                             "strength_clip": float(opts.get("lora_strength", 0.9))}}
        graph["6"]["inputs"]["model"] = ["9", 0]
        graph["4"]["inputs"]["clip"] = ["9", 1]
        graph["5"]["inputs"]["clip"] = ["9", 1]
    return _comfy_run(base, graph, {"seed": seed})


def reference_sheet(refs: list, size: int = 1024) -> Image.Image:
    """The style references tiled on one magenta image (Qwen-Image-Edit takes at most 3 images)."""
    cols = 3
    rows = max(1, (len(refs) + cols - 1) // cols)
    cell = size // cols
    sheet = Image.new("RGB", (size, cell * rows), (255, 0, 255))
    for i, r in enumerate(refs):
        t = r.convert("RGB").resize((cell, cell), Image.LANCZOS)
        sheet.paste(t, ((i % cols) * cell, (i // cols) * cell))
    return sheet


def qwen(images, text, model, opts):
    """Qwen-Image-Edit in ComfyUI (QWEN_COMFY_URL, e.g. a RunPod pod). image1 is the icon, image2 the
    style references as one sheet. opts: steps (30), cfg (4), shift (3.1), seed, lora and lora_strength
    (comma-separated lists), prefix (put before the prompt: a LoRA's trigger), text_encoder, vae. forge.QWEN_DEFAULTS adds the 8-step Lightning LoRA."""
    base = _comfy_url("QWEN_COMFY_URL")
    text = opts.get("prefix", "") + text  # e.g. a LoRA's trigger words ("Pixel Art, ")
    icon = _comfy_upload(base, images[0])
    seed = int(opts.get("seed", int(time.time()) % 2**31))
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": opts.get("text_encoder", "qwen_2.5_vl_7b_fp8_scaled.safetensors"),
                                                     "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": opts.get("vae", "qwen_image_vae.safetensors")}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": float(opts.get("shift", 3.1))}},
        "5": {"class_type": "CFGNorm", "inputs": {"model": ["4", 0], "strength": 1.0}},
        "6": {"class_type": "LoadImage", "inputs": {"image": icon}},
        "7": {"class_type": "ImageScaleToTotalPixels", "inputs": {"image": ["6", 0], "upscale_method": "lanczos",
                                                                  "megapixels": 1.0, "resolution_steps": 1}},
        "9": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": text, "vae": ["3", 0],
                                                                      "image1": ["7", 0]}},
        "10": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0],
                                                                       "image1": ["7", 0]}},
        "11": {"class_type": "VAEEncode", "inputs": {"pixels": ["7", 0], "vae": ["3", 0]}},
        "12": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "positive": ["9", 0], "negative": ["10", 0],
                                                     "latent_image": ["11", 0], "seed": seed,
                                                     "steps": int(opts.get("steps", 30)), "cfg": float(opts.get("cfg", 4)),
                                                     "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "iconforge"}},
    }
    if len(images) > 1:
        sheet = _comfy_upload(base, reference_sheet(images[1:]))
        graph["8"] = {"class_type": "LoadImage", "inputs": {"image": sheet}}
        graph["9"]["inputs"]["image2"] = ["8", 0]
        graph["10"]["inputs"]["image2"] = ["8", 0]
    # LoRAs chain between the model loader and the sampling setup: "lora" and "lora_strength" take
    # comma-separated lists, e.g. lora=qwen-edit-2511-lightning-8step.safetensors,qwen-pixel-art.safetensors
    loras = [x.strip() for x in str(opts.get("lora", "")).split(",") if x.strip()]
    strengths = [float(x) for x in str(opts.get("lora_strength", "")).split(",") if x.strip()]
    prev = ["1", 0]
    for i, name in enumerate(loras):
        node = str(20 + i)
        graph[node] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": prev, "lora_name": name,
                                  "strength_model": strengths[i] if i < len(strengths) else 1.0}}
        prev = [node, 0]
    graph["4"]["inputs"]["model"] = prev
    return _comfy_run(base, graph, {"seed": seed})


PROVIDERS = {"echo": echo, "comfy": comfy, "qwen": qwen, "openai": openai, "fal": fal}
TAKES_REFERENCES = {"openai", "fal", "qwen"}


def split(spec: str):
    """"provider" or "provider@model" -> (provider, model)."""
    provider, _, model = spec.partition("@")
    if provider not in PROVIDERS:
        raise SystemExit("unknown provider %r (have: %s)" % (provider, ", ".join(PROVIDERS)))
    return provider, model or DEFAULT_MODELS[provider]


def run(spec: str, images: list, text: str, opts: dict | None = None):
    provider, model = split(spec)
    if provider not in TAKES_REFERENCES:
        images = images[:1]
    t = time.time()
    img, info = PROVIDERS[provider](images, text, model, opts or {})
    info.update(provider=provider, model=model, seconds=round(time.time() - t, 1))
    return img, info
