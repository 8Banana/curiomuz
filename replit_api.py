"""Python frontend to the repl.it code execution API.

To use this module, create a repl.it account and save your API secret to
replit-api-key.txt.

Account creating page: https://repl.it/site/api
You can find your API secret here: https://repl.it/account/
"""

# Big thanks to __Myst__ for writing most of this code! Figuring out 
# this stuff might have taken us several days without his awesome help.

# This code is based on the node.js replit-client. You can download it 
# to ./node_modules like this:
#   $ npm install replit-client
#
# Stupid docs: https://repl.it/site/blog/api-docs

import base64
import collections
import hashlib
import hmac
import io
import logging
import time

import asks
import curio


FIVE_DAYS = 60 * 60 * 24 * 5
logger = logging.getLogger(__name__)
ReplitResponse = collections.namedtuple("ReplitResponse", "output response")
_known_token = None


async def _generate_token():
    try:
        async with curio.aopen('replit-api-key.txt', 'r') as f:
            api_key = (await f.read()).strip()
    except FileNotFoundError:
        logger.warning("cannot read replit-api-key.txt, code "
                       "evaluation disabled")
        return None

    logger.info("generating an API token")
    now = int(time.time()) * 1000
    result = hmac.new(api_key.encode("utf-8"),
                      str(now).encode("utf-8"),
                      hashlib.sha256)
    msg_mac = base64.b64encode(result.digest()).decode("utf-8")
    return f"{now}:{msg_mac}"


def _is_old(token):
    then = int(token.split(":")[0]) / 1000
    return then + FIVE_DAYS <= time.time()


async def _get_token():
    """Return the API token and generate if needed.

    The token may be a string or None.
    """
    global _known_token
    if _known_token is None or _is_old(_known_token):
        _known_token = await _generate_token()
    return _known_token


async def evaluate_remotely(code, language):
    token = await _get_token()
    if token is None:
        return None

    response = await asks.post("https://api.repl.it/eval", data={
        "auth": token,
        "language": language,
        "code": code,
    })

    output = result = ''
    for msg in response.json():
        if msg["command"] == "output":
            output += msg["data"]
        elif msg["command"] == "result":
            result += msg["data"]
    return ReplitResponse(output, result)


if __name__ == '__main__':
    # simple demo
    code = input(">>> ")
    response = curio.run(evaluate_remotely(code, 'python3'))
    print(response)
