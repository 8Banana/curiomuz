import functools
import inspect
import logging
import string
import sys
import traceback
import types

import curio


logger = logging.getLogger(__name__)

event_handlers = {}
_commands = {}  # {name: (func, usage, min_args, max_args), ...}


class Event(types.SimpleNamespace):
    pass


def _basic_event_handler(command):
    def _inner(func):
        event_handlers.setdefault(command, []).append(func)
        return func
    return _inner


privmsg = _basic_event_handler("PRIVMSG")
join = _basic_event_handler("JOIN")
part = _basic_event_handler("PART")
quit = _basic_event_handler("QUIT")
kick = _basic_event_handler("KICK")


async def _try_except_run(command_name, event, coro):
    try:
        await coro
    except Exception as e:
        logger.exception(f"the {command_name} command failed")
        await event.reply(f"{type(e).__name__}: {e}")


@privmsg
async def _command_dispatcher(event):
    command_name, *params = event.message.split()
    if command_name in _commands:
        func, usage, minargs, maxargs = _commands[command_name]
        if minargs <= len(params) <= maxargs:
            coro = func(event, *params)
            await curio.spawn(_try_except_run(command_name, event, coro))
        else:
            await event.reply("Usage:", usage)


def command(command_name):
    '''Add a channel command, e.g. !test or !log.

    Use this as a decorator, like this:

        @bot.command("!hello")
        async def hi_handler(event, target=None):
            """Say hello to somebody."""
            if target is None:
                await event.reply("Hello World!")
            else:
                await event.reply(f"Hello {target}!")

        bot.add_help_command("!help")

    This would create a usage message like this:

        <n00b>      !help hello
        <this-bot>  !hello [TARGET]: Say hello to somebody.
        <n00b>      !hello
        <this-bot>  Hello World!
        <n00b>      !hello there
        <this-bot>  Hello there!
    '''
    def inner(func):
        required = 0
        optional = 0
        usage = [command_name]

        params = iter(inspect.signature(func).parameters.items())
        next(params)   # skip the event parameter
        for name, param in params:
            if param.default is param.empty:
                usage.append(name.upper())
                required += 1
            else:
                usage.append('[' + name.upper() + ']')
                optional += 1

        _commands[command_name] = (
            func, ' '.join(usage),
            required, required + optional,
        )
        return func

    return inner


def add_help_command(command_name):
    """Add a help command using the command decorator.

    The descriptions are taken from the command functions' docstrings. 
    New commands can be added after adding this help command and they 
    will show up in the help.

    The new help function is returned. For example, you can change its 
    docstring to customize getting help of the help command.
    """

    @command(command_name)
    async def do_help(event, command=None):
        """Display a list of commands or help on a specific command."""
        if command is None:
            commandlist = ', '.join(sorted(_commands))
            await event.reply(
                f"See '{command_name} COMMAND' for help on a specific "
                f"command. List of commands: {commandlist}")
            return

        command = command.strip()
        try:
            infotuple = _commands[command]
        except KeyError:
            # look for it by ignoring punctuation, e.g. test matches !test
            punct = string.punctuation
            for name in _commands:
                if name.strip(punct) == command.strip(punct):
                    infotuple = _commands[name]
                    break
            else:  # no matches
                await event.reply(f"No command called {command} :(")
                return

        func, usage, minargs, maxargs = infotuple
        if func.__doc__ is None:
            doc = "No description provided."
        else:
            doc = ' '.join(func.__doc__.split())
        await event.reply(usage + ": " + doc)

    return do_help


def _parse_sender(sender):
    assert sender.startswith(":")
    sender = sender[1:]

    if "!" in sender:
        nick, user = sender.split("!", 1)
        user, hostname = user.split("@", 1)
        return {"type": "user",
                "nick": nick,
                "user": user,
                "hostname": hostname}
    else:
        return {"type": "server",
                "server": sender}


def _parse_message(raw_msg):
    sender, command, params = raw_msg.strip("\r\n").split(" ", 2)
    try:
        start, end = params.split(" :", 1)
    except ValueError:
        # no " :"
        paramlist = params.split(" ")
    else:
        paramlist = start.split(" ") + [end]

    result = Event(sender=_parse_sender(sender), command=command,
                   params=paramlist)

    # these are most common things for convenience, use params if these 
    # are not enough
    # the target attribute is usually a channel or a nick
    if command in {'JOIN', 'PART', 'QUIT'}:
        (result.target,) = paramlist
    elif command == 'PRIVMSG':
        result.target, result.message = paramlist
    elif command == 'KICK':
        result.channel, result.target, result.reason = paramlist
    return result


class IrcBot:

    def __init__(self, nick, channels):
        self.nick = nick
        self.channels = channels
        self.stream = None

    async def _send(self, *data):
        joined_data = " ".join(data).encode("utf-8")
        if not joined_data.endswith(b"\r\n"):
            joined_data += b"\r\n"
        await self.stream.write(joined_data)

    async def reply(self, event, *data):
        source = event.params[0]
        if source == self.nick:
            source = event.sender["nick"]
        await self._send("PRIVMSG", source, ":" + " ".join(data))

    async def connect(self, host, port=6667):
        logger.info("creating a socket and connecting it to %s:%d",
                    host, port)
        sock = curio.socket.socket()
        await sock.connect((host, port))
        self.stream = sock.as_stream()

        logger.info("sending user information")
        await self._send("NICK", self.nick)
        await self._send("USER", self.nick, "0", "*", ":" + self.nick)

        logger.info("waiting for the end of MOTD")
        # We need to wait for the command that signals the end of the MOTD.
        # This is defined in RFC 2812.
        async for line in self.stream:
            line = line.decode("utf-8")
            event = _parse_message(line)
            if event.command == "376":
                break
            print("(MOTD/notice)", line.rstrip("\r\n"))

        logger.info("joining channels")
        for channel in self.channels:
            await self._send("JOIN", channel)

    async def mainloop(self):
        logger.info("running mainloop")

        async for line in self.stream:
            # replacing is not really correct, but it's better than
            # crashing the whole bot if there are non-UTF8 characters in
            # someone's message
            line = line.decode("utf-8", errors="replace")

            if line.startswith("PING"):
                await self._send(line.replace("PING", "PONG", 1))
                continue

            event = _parse_message(line)
            event.bot = self
            event.reply = functools.partial(self.reply, event)
            print(event)
            for callback in event_handlers.get(event.command, ()):
                await curio.spawn(callback(event))
