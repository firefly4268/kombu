"""
kombu.transport.message
=======================

Message class.

"""
from __future__ import absolute_import, unicode_literals

import sys

from .compression import decompress
from .exceptions import MessageStateError
from .five import reraise, text_t
from .serialization import loads
from .utils.functional import dictfilter

ACK_STATES = frozenset(['ACK', 'REJECTED', 'REQUEUED'])


class Message(object):
    """Base class for received messages."""
    __slots__ = ('_state', 'channel', 'delivery_tag',
                 'content_type', 'content_encoding',
                 'delivery_info', 'headers', 'properties',
                 'body', '_decoded_cache', 'accept', '__dict__')
    MessageStateError = MessageStateError

    errors = None

    def __init__(self, channel, body=None, delivery_tag=None,
                 content_type=None, content_encoding=None, delivery_info={},
                 properties=None, headers=None, postencode=None,
                 accept=None, **kwargs):
        self.errors = [] if self.errors is None else self.errors
        self.channel = channel
        self.delivery_tag = delivery_tag
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.delivery_info = delivery_info
        self.headers = headers or {}
        self.properties = properties or {}
        self._decoded_cache = None
        self._state = 'RECEIVED'
        self.accept = accept

        compression = self.headers.get('compression')
        if not self.errors and compression:
            try:
                body = decompress(body, compression)
            except Exception:
                self.errors.append(sys.exc_info())

        if not self.errors and postencode and isinstance(body, text_t):
            try:
                body = body.encode(postencode)
            except Exception:
                self.errors.append(sys.exc_info())
        self.body = body

    def _reraise_error(self, callback=None):
        try:
            reraise(*self.errors[0])
        except Exception as exc:
            if not callback:
                raise
            callback(self, exc)

    def ack(self, multiple=False):
        """Acknowledge this message as being processed.,
        This will remove the message from the queue.

        :raises MessageStateError: If the message has already been
            acknowledged/requeued/rejected.

        """
        if self.channel.no_ack_consumers is not None:
            try:
                consumer_tag = self.delivery_info['consumer_tag']
            except KeyError:
                pass
            else:
                if consumer_tag in self.channel.no_ack_consumers:
                    return
        if self.acknowledged:
            raise self.MessageStateError(
                'Message already acknowledged with state: {0._state}'.format(
                    self))
        self.channel.basic_ack(self.delivery_tag, multiple=multiple)
        self._state = 'ACK'

    def ack_log_error(self, logger, errors, multiple=False):
        try:
            self.ack(multiple=multiple)
        except errors as exc:
            logger.critical("Couldn't ack %r, reason:%r",
                            self.delivery_tag, exc, exc_info=True)

    def reject_log_error(self, logger, errors, requeue=False):
        try:
            self.reject(requeue=requeue)
        except errors as exc:
            logger.critical("Couldn't reject %r, reason: %r",
                            self.delivery_tag, exc, exc_info=True)

    def reject(self, requeue=False):
        """Reject this message.

        The message will be discarded by the server.

        :raises MessageStateError: If the message has already been
            acknowledged/requeued/rejected.

        """
        if self.acknowledged:
            raise self.MessageStateError(
                'Message already acknowledged with state: {0._state}'.format(
                    self))
        self.channel.basic_reject(self.delivery_tag, requeue=requeue)
        self._state = 'REJECTED'

    def requeue(self):
        """Reject this message and put it back on the queue.

        You must not use this method as a means of selecting messages
        to process.

        :raises MessageStateError: If the message has already been
            acknowledged/requeued/rejected.

        """
        if self.acknowledged:
            raise self.MessageStateError(
                'Message already acknowledged with state: {0._state}'.format(
                    self))
        self.channel.basic_reject(self.delivery_tag, requeue=True)
        self._state = 'REQUEUED'

    def decode(self):
        """Deserialize the message body, returning the original
        python structure sent by the publisher.

        Note: The return value is memoized, use `_decode` to force
        re-evaluation.

        """
        if not self._decoded_cache:
            self._decoded_cache = self._decode()
        return self._decoded_cache

    def _decode(self):
        return loads(self.body, self.content_type,
                     self.content_encoding, accept=self.accept)

    @property
    def acknowledged(self):
        """Set to true if the message has been acknowledged."""
        return self._state in ACK_STATES

    @property
    def payload(self):
        """The decoded message body."""
        return self._decoded_cache if self._decoded_cache else self.decode()

    def __repr__(self):
        return '<{0} object at {1:#x} with details {2!r}>'.format(
            type(self).__name__, id(self), dictfilter(
                state=self._state,
                content_type=self.content_type,
                delivery_tag=self.delivery_tag,
                body_length=len(self.body) if self.body is not None else None,
                properties=dictfilter(
                    correlation_id=self.properties.get('correlation_id'),
                    type=self.properties.get('type'),
                ),
                delivery_info=dictfilter(
                    exchange=self.delivery_info.get('exchange'),
                    routing_key=self.delivery_info.get('routing_key'),
                ),
            ),
        )
