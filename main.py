"""Run the bot.

Channel messages, join/part/quit messages and the like are saved to 
files under irclogs and printed to stdout. Debugging messages are 
printed to stderr and saved in botlog.txt.
"""

import atexit
import collections
import glob
import logging
import os
import time

import curio
from curio import socket, subprocess

import bot


logger = logging.getLogger(__name__)

LOG_LEN = 1000
logs = {}  # {channel: deque, ...}


def _format_msg(msg):
    return f"[%s] %s\n" % (time.strftime('%d %b %H:%M:%S'), msg)


def _log_filename(channel):
    return os.path.join('irclogs', channel + '.txt')


async def log_msg(channel, msg):
    try:
        log = logs[channel]
    except KeyError:
        log = collections.deque(maxlen=LOG_LEN)
        try:
            async with curio.aopen(_log_filename(channel), 'r') as f:
                async for line in f:
                    log.append(line)
        except FileNotFoundError:
            # We are running for the first time and nothing is logged
            # yet.
            pass
        logs[channel] = log

    print(f"({channel})", msg)
    log.append(_format_msg(msg))


@atexit.register
def save_logs():
    logger.info("saving logs")
    try:
        os.mkdir('irclogs')
    except FileExistsError:
        pass

    for channel, lines in logs.items():
        lines.append(_format_msg("* Shutting down."))
        with open(_log_filename(channel), 'w') as f:
            f.writelines(lines)


async def termbin(iterable):
    """Paste the content of iterable to termbin and return URL.

    The iterable can be asynchronous or synchronous.
    """
    try:
        logger.info("sending %d lines to termbin", len(iterable))
    except TypeError:
        # probably a file object or some other iterator
        logger.info("sending content of %r to termbin", iterable)

    async with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        await sock.connect(('termbin.com', 9999))

        if hasattr(type(iterable), '__aiter__'):
            async for string in iterable:
                # replace is not the best possible way, but at least
                # better than failing to termbin anything
                await sock.sendall(string.encode('utf-8', errors='replace'))
        else:
            for string in iterable:
                await sock.sendall(string.encode('utf-8', errors='replace'))

        byteurl = await sock.recv(1024)
        return byteurl.decode('ascii').strip()


@bot.command("!log")
async def termbin_log(event, channel=None):
    """Termbin the log of the channel."""
    if channel is None:
        channel_given = False
        channel = event.target
    else:
        channel_given = True

    lines = logs.get(channel, [])
    if lines:
        await event.reply(await termbin(lines))
    else:
        # termbin says "Use netcat." if we send it nothing
        msg = f"Nothing is logged from {channel} yet!"
        if not channel_given:
            msg += (" You can use '!log CHANNEL' to get logs from a "
                    "specific channel.")
        await event.reply(msg)


@bot.command("!src")
async def link_source(event):
    """Send a link to my code :D"""
    linkbytes = await subprocess.check_output([
        'git', 'config', '--get', 'remote.origin.url'])
    link = linkbytes.decode('utf-8').strip()
    await event.reply(f"I'm from {link}.")


@bot.command("!wtf")
async def do_wtf(event, acronym):
    """Translate an acronym to English."""
    acronym = acronym.upper()
    async with curio.aopen('wtf-words.txt', 'r') as f:
        async for line in f:
            if line.upper().startswith(acronym + '\t'):
                await event.reply(line.replace("\t", ": ").strip())
                return
    await event.reply(f"I have no idea what {acronym} means :(")


bot.add_help_command("!help")


@bot.join
@bot.part
@bot.quit
async def info_handler(event):
    logmsg = "* {} {}s".format(
        event.sender['nick'], event.command.lower())
    await log_msg(event.target, logmsg)


@bot.kick
async def kick_handler(event):
    logmsg = "{} {}s {} (reason: {})".format(
        event.sender['nick'], event.command.lower(),
        event.target, event.reason)
    await log_msg(event.channel, logmsg)


@bot.privmsg
async def privmsg_handler(event):
    await log_msg(event.target, "<%s> %s" % (
        event.sender['nick'], event.message))


async def main():
    logging.basicConfig(
        filename='botlog.txt', datefmt='%d %b %H:%M:%S', level=logging.DEBUG,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s")
    # unfortunately it's not possible to log to file and stderr with 
    # just basicConfig :(
    logging.getLogger().addHandler(logging.StreamHandler())

    bananabot = bot.IrcBot('curiomuz', ['#8banana'])
    await bananabot.connect('chat.freenode.net')
    await bananabot.mainloop()


if __name__ == '__main__':
    curio.run(main())
