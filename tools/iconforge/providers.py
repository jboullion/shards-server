"""
Image models for the restyle step. Each provider takes (images, prompt, model) and returns
(PIL image, info). The first image is the icon to redraw; any others are style references.
Standard library HTTP only (urllib), so the system Python with Pillow + numpy is enough.

    echo     no model: returns the input. Free; for trying the pipeline end to end.
    comfy    a local ComfyUI (127.0.0.1:8188): SDXL img2img with an optional LoRA. No style
             references (img2img only sees the icon); the prompt carries the style.
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

DEFAULT_MODELS = {"echo": "none", "comfy": "waiIllustriousSDXL_v160.safetensors",
                  "openai": "gpt-image-2.5-sunburst", "fal": "fal-ai/flux-pro/kontext"}
COMFY_URL = "http://127.0.0.1:8188"
TIMEOUT = 600


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _request(url, data=None, headers=None, method=None, timeout=TIMEOUT) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        # error bodies don't echo keys; keep them short
        raise RuntimeError("%s: HTTP %d %s" % (url.split("?")[0], e.code, e.read()[:800].decode("utf-8", "replace")))


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


def fal(images, text, model, opts):
    key = env.require("FAL_AI_API_KEY")
    uris = ["data:image/png;base64," + base64.b64encode(_png(im)).decode() for im in images]
    body = {"prompt": text, "image_url": uris[0], "output_format": "png", "num_images": 1}
    if len(uris) > 1:
        body["image_urls"] = uris
    out = _json("https://fal.run/" + model, body, {"Authorization": "Key " + key})
    img = _request(out["images"][0]["url"], timeout=120)
    return Image.open(io.BytesIO(img)), {"seed": out.get("seed")}


def comfy(images, text, model, opts):
    """SDXL img2img in a local ComfyUI. opts: denoise (0.55), steps (28), cfg (6), seed,
    lora (file name in models/loras), lora_strength (0.9), negative."""
    src = images[0].convert("RGB").resize((1024, 1024), Image.LANCZOS)
    body, ctype = _multipart({"overwrite": "true"}, [("image", "iconforge_%s.png" % uuid.uuid4().hex[:8], _png(src), "image/png")])
    up = json.loads(_request(COMFY_URL + "/upload/image", body, {"Content-Type": ctype}))
    neg = opts.get("negative", "photo, photorealistic, 3d render, blurry, soft shading, gradient background, text, watermark")
    seed = int(opts.get("seed", int(time.time()) % 2**31))
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "2": {"class_type": "LoadImage", "inputs": {"image": up["name"]}},
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
    pid = _json(COMFY_URL + "/prompt", {"prompt": graph, "client_id": "iconforge"})["prompt_id"]
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        hist = _json(COMFY_URL + "/history/" + pid)
        if pid in hist:
            entry = hist[pid]
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError("comfy: %s" % json.dumps(entry["status"])[:800])
            for node in entry.get("outputs", {}).values():
                for im in node.get("images", []):
                    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im["subfolder"], "type": im["type"]})
                    return Image.open(io.BytesIO(_request(COMFY_URL + "/view?" + q))), {"seed": seed}
        time.sleep(1.5)
    raise RuntimeError("comfy: no result within %ds" % TIMEOUT)


PROVIDERS = {"echo": echo, "comfy": comfy, "openai": openai, "fal": fal}
TAKES_REFERENCES = {"openai", "fal"}


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
