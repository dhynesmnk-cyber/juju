"""A scripted fake model for the assistant's tests: OpenAI-format chat completion replies, in order,
and every request kept. No test uses the network (httpx's MockTransport answers)."""
import json

import httpx


def text_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                              "content": text}}]})


def tool_reply(*calls: tuple[str, object], content: str = "", **extra) -> httpx.Response:
    tool_calls = [{"id": f"call_{i}", "type": "function",
                   "function": {"name": name, "arguments": args if isinstance(args, str)
                                else json.dumps(args)}} for i, (name, args) in enumerate(calls)]
    return httpx.Response(200, json={"choices": [{"message": {
        "role": "assistant", "content": content, "tool_calls": tool_calls, **extra}}]})


class Script:
    """Replies in order; every request is kept."""

    def __init__(self, *replies: httpx.Response | Exception):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def body(self, n: int) -> dict:
        return json.loads(self.requests[n].read())
