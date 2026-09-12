"""Fixed counted-pot shares, reserving rounding for pots not yet planted."""


def counted_parts(total, count, participants, quantum):
    """Split a prefix of equal shares without constructing anonymous identities."""
    if total is None:
        return [None] * participants
    whole, remainder = divmod(int(total / quantum), count)
    return [(whole + (index < remainder)) * quantum for index in range(participants)]
