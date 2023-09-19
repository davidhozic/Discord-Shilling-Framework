"""
Automatic GUILD generation.
"""
from contextlib import suppress
from typing import Any, Union, List, Optional, Dict
from typeguard import typechecked
from datetime import timedelta, datetime

from ..message import TextMESSAGE, VoiceMESSAGE, BaseMESSAGE
from ..logging.tracing import TraceLEVELS, trace
from ..misc import async_util, instance_track, doc, attributes
from ..events import *

from .guilduser import GUILD

import _discord as discord
import asyncio

from .. import web
from .. import logging

import re


GUILD_JOIN_INTERVAL = timedelta(seconds=45)
GUILD_MAX_AMOUNT = 100

@instance_track.track_id
@doc.doc_category("Auto objects", path="guild")
class AutoGUILD:
    """
    .. versionchanged:: v2.11
        Now works like GUILD and USER.

    Represents multiple guilds (servers) based on a text pattern.

    Parameters
    --------------
    include_pattern: str
        Regex pattern to use for searching guild names that are to be included.
        This is also checked before joining a new guild if ``auto_guild`` is given.

        For example you can do write ``.*`` to match ALL guilds you are joined into or specify
        (parts of) guild names separated with ``|`` like so: "name1|name2|name3|name4"

    exclude_pattern: Optional[str] = None
        Regex pattern to use for searching guild names that are
        **NOT** to be excluded.

        .. note::
            If both include_pattern and exclude_pattern yield a match,
            the guild will be excluded from match.

    remove_after: Optional[Union[timedelta, datetime]] = None
        When to remove this object from the shilling list.
    logging: Optional[bool] = False
        Set to True if you want the guilds generated to log
        sent messages.
    auto_join: Optional[web.GuildDISCOVERY] = None
        .. versionadded:: v2.5

        Optional :class:`~daf.web.GuildDISCOVERY` object which will automatically discover
        and join guilds though the browser.
        This will open a Google Chrome session.
    """
    __slots__ = (
        "include_pattern",
        "exclude_pattern",
        "remove_after",
        "_messages_uninitialized",
        "logging",
        "_created_at",
        "update_semaphore",
        "parent",
        "auto_join",
        "guild_query_iter",
        "guild_join_count",
        "invite_track",
        "_messages",
        "_removed_messages",
        "removal_buffer_length",
        "_removal_timer_handle",
        "_guild_join_timer_handle",
        "_event_ctrl",
    )

    @typechecked
    def __init__(
        self,
        include_pattern: str,
        exclude_pattern: Optional[str] = None,
        remove_after: Optional[Union[timedelta, datetime]] = None,
        messages: Optional[List[Union[TextMESSAGE, VoiceMESSAGE]]] = None,
        logging: Optional[bool] = False,
        auto_join: Optional[web.GuildDISCOVERY] = None,
        invite_track: Optional[List[str]] = None,
        removal_buffer_length: int = 50
    ) -> None:
        # Remove spaces around OR
        self.include_pattern = re.sub(r"\s*\|\s*", '|', include_pattern) if include_pattern else None
        self.exclude_pattern = re.sub(r"\s*\|\s*", '|', exclude_pattern) if exclude_pattern else None
        self.remove_after = remove_after
        self.invite_track = invite_track
        # Uninitialized template messages that get copied for each found guild.
        self._messages_uninitialized = messages if messages is not None else []
        self.logging = logging
        self.auto_join = auto_join
        self._created_at = datetime.now()
        self.parent = None
        self.guild_query_iter = None
        self.guild_join_count = 0
        self._messages: List[BaseMESSAGE] = []
        self._removed_messages: List[BaseMESSAGE] = []
        self.removal_buffer_length = removal_buffer_length
        self._removal_timer_handle: asyncio.Task = None
        self._guild_join_timer_handle: asyncio.Task = None
        self._event_ctrl: EventController = None
        attributes.write_non_exist(self, "update_semaphore", asyncio.Semaphore(1))

    @property
    def created_at(self) -> datetime:
        """
        Returns the datetime of when the object has been created.
        """
        return self._created_at

    @property
    def messages(self) -> List[Union[TextMESSAGE, VoiceMESSAGE]]:
        """
        .. versionadded:: 3.0

        Returns all the (initialized) message objects.
        """
        return self._messages[:]

    @property
    def guilds(self) -> List[discord.Guild]:
        "Returns all the guilds that match the include_pattern and not exclude_pattern"
        client: discord.Client = self.parent.client
        return [
            g for g in client.guilds
            if re.search(self.include_pattern, g.name) is not None and
               (self.exclude_pattern is None or re.search(self.exclude_pattern, g.name) is None)
        ]

    # API
    @typechecked
    def add_message(self, message: BaseMESSAGE) -> asyncio.Future:
        """
        Adds a message to the message list.

        .. warning::
            To use this method, the guild must already be added to the
            framework's shilling list (or initialized).

        |ASYNC_API|

        Parameters
        --------------
        message: BaseMESSAGE
            Message object to add.

        Returns
        --------
        Awaitable
            An awaitable object which can be used to await for execution to finish.
            To wait for the execution to finish, use ``await`` like so: ``await method_name()``.

        Raises
        --------------
        TypeError
            Raised when the message is not of type the guild allows.
        Other
            Raised from message.initialize() method.
        """
        return self._event_ctrl.emit(EventID.message_added, self, message)

    @typechecked
    def remove_message(self, message: BaseMESSAGE) -> asyncio.Future:
        """
        Removes a message from the message list.

        .. versionchanged:: 3.0

            The function is now async.

        |ASYNC_API|

        Parameters
        --------------
        message: BaseMESSAGE
            Message object to remove.

        Returns
        --------
        Awaitable
            An awaitable object which can be used to await for execution to finish.
            To wait for the execution to finish, use ``await`` like so: ``await method_name()``.

        Raises
        --------------
        TypeError
            Raised when the message is not of type the guild allows.
        ValueError
            Raised when the message is not present in the list.
        """
        return self._event_ctrl.emit(EventID.message_removed, self, message)

    def update(self, init_options = None, **kwargs) -> asyncio.Future:
        """
        Updates the object with new initialization parameters.

        |ASYNC_API|

        Returns
        --------
        Awaitable
            An awaitable object which can be used to await for execution to finish.
            To wait for the execution to finish, use ``await`` like so: ``await method_name()``.

        .. WARNING::
            After calling this method the entire object is reset
            (this includes it's GUILD objects in cache).
        """
        return self._event_ctrl.emit(EventID.server_update, self, init_options, **kwargs)

    # Non public methods
    def _reset_auto_join_timer(self):
        "Resets the periodic auto guild join timer."
        self._guild_join_timer_handle = async_util.call_at(
            self._event_ctrl.emit,
            GUILD_JOIN_INTERVAL,
            EventID.auto_guild_start_join,
            self
        )

    async def initialize(self, parent: Any, event_ctrl: EventController):
        """
        Initializes the object.

        Raises
        --------
        ValueError
            Auto-join guild functionality requires the account to be
            provided with username and password.
        """
        self._event_ctrl = event_ctrl
        self.parent = parent
        if self.auto_join is not None:
            await self.auto_join.initialize(self)
            self.guild_query_iter = self.auto_join._query_request()

        for message in self._messages_uninitialized:
            try:
                await self._on_add_message(self, message)
            except (TypeError, ValueError) as exc:
                trace(f" Unable to initialize message {message}, in {self}", TraceLEVELS.WARNING, exc)

        if self.remove_after is not None:
            self._removal_timer_handle = (
                async_util.call_at(
                    event_ctrl.emit,
                    self.remove_after,
                    EventID.server_removed,
                    self
                )
            )

        if self.auto_join is not None:
            self._reset_auto_join_timer()
            event_ctrl.add_listener(EventID.auto_guild_start_join, self._join_guilds, lambda ag: ag is self)

        event_ctrl.add_listener(EventID.message_ready, self._advertise, lambda server, m: server is self)
        event_ctrl.add_listener(EventID.message_added, self._on_add_message, lambda server, m: server is self)
        event_ctrl.add_listener(EventID.server_update, self._on_update, lambda server, *args, **kwargs: server is self)
        event_ctrl.add_listener(EventID.message_removed, self._on_remove_message, lambda server, m: server is self)

    def _generate_guild_log_context(self, guild: discord.Guild):
        return {
                "name": guild.name,
                "id": guild.id,
                "type": "GUILD"
        }

    def _filter_message_context(self, guild: discord.Guild, message_ctx: dict) -> Dict:
        message_ctx = message_ctx.copy()
        message_ctx["channels"] = message_ctx["channels"].copy()

        channel_ctx = message_ctx["channels"]
        guild_channels = set(x.id for x in guild.channels)
        channel_ctx["successful"] = [x for x in channel_ctx["successful"] if x["id"] in guild_channels]
        channel_ctx["failed"] = [x for x in channel_ctx["failed"] if x["id"] in guild_channels]

        return message_ctx if channel_ctx["successful"] or channel_ctx["failed"] else None

    @async_util.with_semaphore("update_semaphore")
    async def _advertise(self, _, message: BaseMESSAGE):
        """
        Advertises thru all the GUILDs.
        """
        if message._check_state():
            await self.remove_message(message)
            return

        author_ctx = self.parent.generate_log_context()
        message_context = await message._send()
        if message_context and self.logging:
            for guild in self.guilds:
                guild_ctx = self._generate_guild_log_context(guild)
                message_guild_ctx = self._filter_message_context(guild, message_context)
                if message_guild_ctx:
                    await logging.save_log(guild_ctx, message_guild_ctx, author_ctx)

        message._reset_timer()

    def _get_channels(self, *types):
        for guild in self.guilds:
            for channel in guild.channels:
                if isinstance(channel, types):
                    yield channel

    async def _on_add_message(self, _, message: BaseMESSAGE):
        await message.initialize(parent=self, event_ctrl=self._event_ctrl, channel_getter=self._get_channels)
        self._messages.append(message)
        with suppress(ValueError):  # Readd the removed message
            self._removed_messages.remove(message)

    async def _on_remove_message(self, _, message: BaseMESSAGE):
        "Event loop handler for removing messages"
        trace(f"Removing message {message} from {self}", TraceLEVELS.NORMAL)
        self._messages.remove(message)
        self._removed_messages.append(message)
        if len(self._removed_messages) > self.removal_buffer_length:
            trace(f"Removing oldest record of removed messages {self._removed_messages[0]}", TraceLEVELS.DEBUG)
            del self._removed_messages[0]

        await message._close()

    async def _on_update(self, _, init_options, **kwargs):
        await self._close()
        try:
            # Update the guild
            if "invite_track" not in kwargs:
                kwargs["invite_track"] = self.invite_track

            kwargs["messages"] = kwargs.pop("messages", self.messages + self._messages_uninitialized)
            if init_options is None:
                init_options = {"parent": self.parent, "event_ctrl": self._event_ctrl}

            await async_util.update_obj_param(self, init_options=init_options, **kwargs)
        except Exception:
            await self.initialize(self.parent, event_ctrl=self._event_ctrl)
            raise
    
    @async_util.with_semaphore("update_semaphore")
    async def _join_guilds(self, _):
        """
        Coroutine that joins new guilds thru the web layer.
        """
        # Join Guilds
        discovery = self.auto_join
        selenium: web.SeleniumCLIENT = self.parent.selenium
        client: discord.Client = self.parent.client
        if (
            self.guild_query_iter is None or  # No auto_join provided or iterated though all guilds
            self.guild_join_count == discovery.limit or
            len(client.guilds) == GUILD_MAX_AMOUNT
        ):
            self._event_ctrl.remove_listener(EventID.auto_guild_start_join, self._join_guilds)
            return

        async def get_next_guild():
            try:
                # Get next result from top.gg
                yielded: web.QueryResult = await self.guild_query_iter.__anext__()
                if (
                    re.search(self.include_pattern, yielded.name) is None or
                    (
                        self.exclude_pattern is not None and
                        re.search(self.exclude_pattern, yielded.name) is not None
                    )
                ):
                    return None

                return yielded
            except StopAsyncIteration:
                trace(f"Iterated though all found guilds -> stopping guild join in {self}.", TraceLEVELS.NORMAL)
                self.guild_query_iter = None

        if (yielded := await get_next_guild()) is None:
            return

        no_error = True
        # Not already joined in the guild
        if client.get_guild(yielded.id) is None:
            try:
                invite_url = await selenium.fetch_invite_link(yielded.url)
                if invite_url is None:
                    raise RuntimeError("Fetching invite link failed")

                await selenium.random_server_click()
                await selenium.join_guild(invite_url)
                await asyncio.sleep(1)
                if client.get_guild(yielded.id) is None:
                    raise RuntimeError(
                        "No error detected in browser,"
                        "but the guild can not be seen by the API wrapper."
                    )
            except Exception as exc:
                no_error = False
                trace(
                    f"Joining guild raised an error. (Guild '{yielded.name}')",
                    TraceLEVELS.ERROR,
                    exc
                )

        if no_error:
            # Don't count errored joins but count guilds we are already joined if they match the pattern
            self.guild_join_count += 1

        self._reset_auto_join_timer()

    @async_util.with_semaphore("update_semaphore")
    async def _close(self):
        """
        Closes any lower-level async objects.
        """
        self._event_ctrl.remove_listener(EventID.message_ready, self._advertise)
        self._event_ctrl.remove_listener(EventID.message_removed, self._on_remove_message)
        self._event_ctrl.remove_listener(EventID.auto_guild_start_join, self._join_guilds)
        self._event_ctrl.remove_listener(EventID.message_added, self._on_add_message)
        self._event_ctrl.remove_listener(EventID.server_update, self._on_update)

        if self._removal_timer_handle is not None and not self._removal_timer_handle.cancelled():
            self._removal_timer_handle.cancel()
            await asyncio.gather(self._removal_timer_handle, return_exceptions=True)
        
        if self._guild_join_timer_handle is not None and not self._guild_join_timer_handle.cancelled():
            self._guild_join_timer_handle.cancel()
            await asyncio.gather(self._guild_join_timer_handle, return_exceptions=True)

        if self.auto_join is not None:
            await self.auto_join._close()

        for message in self._messages:
            await message._close()
