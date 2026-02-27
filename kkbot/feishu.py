"""Feishu/Lark channel via WebSocket long connection."""

import asyncio
import base64
import json
import re
import threading
from collections import OrderedDict
from typing import Any, Awaitable, Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    Emoji,
    GetMessageResourceRequest,
)
from loguru import logger

# Signature: (sender_id, chat_id, text, images_b64) -> None
MessageHandler = Callable[[str, str, str, list[str]], Awaitable[None]]

SELF_OPEN_ID = "ou_d0e9377c7527efb15649969ee9b08dc1"
_AT_RE = re.compile(r"@_user_\d+\s*")
_MENTION_RE = re.compile(r"<at:([\w]+)>")
# Trigger card rendering if message contains headings or code blocks
_CARD_RE = re.compile(r"(^#{1,6}\s|```)", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_RE = re.compile(r"(```[\s\S]*?```)")

# ---------------------------------------------------------------------------
# Card rendering (markdown → Feishu card elements)
# ---------------------------------------------------------------------------


def _md_to_elements(content: str) -> list[dict]:
    """Convert markdown to Feishu card elements, protecting code blocks."""
    placeholders: list[str] = []
    protected = content
    for m in _CODE_RE.finditer(content):
        placeholders.append(m.group(1))
        protected = protected.replace(m.group(1), f"\x00CB{len(placeholders) - 1}\x00", 1)

    elements: list[dict] = []
    last = 0
    for m in _HEADING_RE.finditer(protected):
        if before := protected[last : m.start()].strip():
            elements.append({"tag": "markdown", "content": before})
        elements.append(
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**{m.group(2).strip()}**"}}
        )
        last = m.end()
    if tail := protected[last:].strip():
        elements.append({"tag": "markdown", "content": tail})

    # Restore code blocks in markdown elements
    for el in elements:
        if el.get("tag") == "markdown":
            for i, cb in enumerate(placeholders):
                el["content"] = el["content"].replace(f"\x00CB{i}\x00", cb)

    return elements or [{"tag": "markdown", "content": content}]


# ---------------------------------------------------------------------------
# Post message parsing (incoming rich text)
# ---------------------------------------------------------------------------


def _extract_post(data: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from a Feishu post message."""

    def _parse(lc: dict) -> tuple[str, list[str]]:
        texts, imgs = [], []
        if lc.get("title"):
            texts.append(lc["title"])
        for block in lc.get("content", []):
            for el in block if isinstance(block, list) else []:
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img":
                    if k := el.get("image_key"):
                        imgs.append(k)
        return " ".join(texts).strip(), imgs

    for src in [data, data.get("zh_cn", {}), data.get("en_us", {}), data.get("ja_jp", {})]:
        t, i = _parse(src)
        if t or i:
            return t, i
    return "", []


# ---------------------------------------------------------------------------
# FeishuBot
# ---------------------------------------------------------------------------


class FeishuBot:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id, self.app_secret = app_id, app_secret
        self._client: lark.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._dedup: OrderedDict[str, None] = OrderedDict()
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        if not self.app_id or not self.app_secret:
            logger.error("Feishu credentials not configured")
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(
                lambda data: asyncio.run_coroutine_threadsafe(self._handle(data), self._loop)
            )
            .build()
        )
        ws = lark.ws.Client(
            self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING
        )

        def _ws_thread():
            while self._running:
                try:
                    ws.start()
                except Exception as e:
                    logger.warning("WS error: {}", e)
                if self._running:
                    import time

                    time.sleep(5)

        threading.Thread(target=_ws_thread, daemon=True).start()
        logger.info("Feishu bot started (WebSocket)")
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False

    # --- incoming -----------------------------------------------------------

    async def _handle(self, data: Any) -> None:
        try:
            msg, sender = data.event.message, data.event.sender
            mid = msg.message_id
            if mid in self._dedup:
                return
            self._dedup[mid] = None
            while len(self._dedup) > 1000:
                self._dedup.popitem(last=False)

            if sender.sender_type == "bot":
                return

            if msg.chat_type == "group":
                mentions = msg.mentions or []
                if not any(
                    getattr(getattr(m, "id", None), "open_id", None) == SELF_OPEN_ID
                    for m in mentions
                ):
                    return

            await self._react(mid)
            content_json = json.loads(msg.content or "{}")
            sender_id = getattr(getattr(sender, "sender_id", None), "open_id", None) or "unknown"
            mtype = msg.message_type
            text_parts: list[str] = []
            images_b64: list[str] = []

            if mtype == "text":
                text_parts.append(_AT_RE.sub("", content_json.get("text", "")).strip())
            elif mtype == "post":
                t, img_keys = _extract_post(content_json)
                if t:
                    text_parts.append(_AT_RE.sub("", t).strip())
                for k in img_keys:
                    if b64 := await self._img_b64(mid, k):
                        images_b64.append(b64)
            elif mtype == "image":
                if b64 := await self._img_b64(mid, content_json.get("image_key", "")):
                    images_b64.append(b64)
                text_parts.append("[image]")
            else:
                text_parts.append(f"[{mtype}]")

            text = "\n".join(p for p in text_parts if p).strip()
            if not text and not images_b64:
                return

            reply_to = msg.chat_id if msg.chat_type == "group" else sender_id
            if self._handler:
                await self._handler(sender_id, reply_to, text, images_b64)
        except Exception:
            logger.exception("Error handling Feishu message")

    async def _img_b64(self, message_id: str, image_key: str) -> str:
        if not image_key:
            return ""

        def _sync() -> bytes | None:
            try:
                req = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(image_key)
                    .type("image")
                    .build()
                )
                resp = self._client.im.v1.message_resource.get(req)
                if resp.success():
                    d = resp.file
                    return d.read() if hasattr(d, "read") else bytes(d)
                logger.error("Image dl failed: {} {}", resp.code, resp.msg)
            except Exception as e:
                logger.error("Image dl error: {}", e)

        data = await asyncio.get_event_loop().run_in_executor(None, _sync)
        return base64.b64encode(data).decode() if data else ""

    # --- outgoing -----------------------------------------------------------

    async def send(self, chat_id: str, text: str) -> None:
        if not self._client:
            logger.warning("Client not ready")
            return
        id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
        loop = asyncio.get_event_loop()
        if _MENTION_RE.search(text):
            content = json.dumps(self._build_post(text), ensure_ascii=False)
            await loop.run_in_executor(None, self._send_raw, id_type, chat_id, "post", content)
        elif _CARD_RE.search(text):
            card = {"config": {"wide_screen_mode": True}, "elements": _md_to_elements(text)}
            content = json.dumps(card, ensure_ascii=False)
            await loop.run_in_executor(
                None, self._send_raw, id_type, chat_id, "interactive", content
            )
        else:
            content = json.dumps({"text": text.strip()}, ensure_ascii=False)
            await loop.run_in_executor(None, self._send_raw, id_type, chat_id, "text", content)

    async def send_image(self, chat_id: str, image_path: str) -> None:
        if not self._client:
            return
        id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
        loop = asyncio.get_event_loop()
        if key := await loop.run_in_executor(None, self._upload_image, image_path):
            content = json.dumps({"image_key": key})
            await loop.run_in_executor(None, self._send_raw, id_type, chat_id, "image", content)

    def _build_post(self, text: str) -> dict:
        """Build a Feishu post message with @-mention support."""
        parts = _MENTION_RE.split(text)
        content = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    content.append({"tag": "text", "text": part})
            else:
                content.append({"tag": "at", "user_id": part})
        return {"zh_cn": {"title": "", "content": [content]}}

    def _send_raw(self, id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        try:
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                logger.error("Send failed: {} {}", resp.code, resp.msg)
                return False
            return True
        except Exception as e:
            logger.error("Send error: {}", e)
            return False

    def _upload_image(self, path: str) -> str | None:
        try:
            with open(path, "rb") as f:
                req = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder().image_type("message").image(f).build()
                    )
                    .build()
                )
                resp = self._client.im.v1.image.create(req)
                return resp.data.image_key if resp.success() else None
        except Exception as e:
            logger.error("Image upload error: {}", e)

    async def _react(self, message_id: str, emoji: str = "THUMBSUP") -> None:
        if not self._client:
            return

        def _sync():
            try:
                req = (
                    CreateMessageReactionRequest.builder()
                    .message_id(message_id)
                    .request_body(
                        CreateMessageReactionRequestBody.builder()
                        .reaction_type(Emoji.builder().emoji_type(emoji).build())
                        .build()
                    )
                    .build()
                )
                self._client.im.v1.message_reaction.create(req)
            except Exception:
                pass

        await asyncio.get_event_loop().run_in_executor(None, _sync)
