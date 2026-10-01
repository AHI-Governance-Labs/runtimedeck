"""Separate llama-cli response and reasoning from startup diagnostics."""

import re


def split_inference_output(raw):
    marker = re.search(r"(?m)^> .*\n", raw)
    if not marker:
        return "", ""
    body = raw[marker.end():]
    body = re.split(r"\[ Prompt:|(?m:^Exiting\.\.\.)|(?m:^\[exit )", body)[0]
    reasoning = re.search(r"\[Start thinking\](.*?)(?:\[End thinking\]|$)", body, re.S)
    thoughts = reasoning.group(1).strip() if reasoning else ""
    answer = body[reasoning.end():].strip() if reasoning else body.strip()
    return answer, thoughts
