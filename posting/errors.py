"""How a platform call can fail. The publisher decides what to do with each kind:

  Retryable  nothing was published (network, rate limit, server error) → try again later
  Permanent  nothing was published, trying again won't help (bad token, rejected video) → needs you
  Ambiguous  it MAY have been published (connection lost on the final step) → never retried
             automatically; you check the channel and type posted or retry in the Sheet
"""


class PostError(Exception):
    pass


class Retryable(PostError):
    def __init__(self, msg, wait_minutes=10):
        super().__init__(msg)
        self.wait_minutes = wait_minutes


class Permanent(PostError):
    pass


class Ambiguous(PostError):
    pass


class LoginNeeded(Permanent):
    """The platform login expired / was removed. Nothing is wrong with the clip: the post WAITS (it is not
    a "needs you" per clip) and goes out by itself once you sign in again (Desktop: Reconnect YouTube)."""
