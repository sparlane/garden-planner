// Reading a run of numbered units the way it is written on a bench card. A
// pot's number is its `pk` — issued once, never reused, unique across the
// nursery — so a run of them is named by the numbers written on the pots
// rather than by their opaque codes. Filling a bench and emptying a tray into
// one both start here, and a range that parsed differently on the two screens
// would give the same card two meanings.

interface NumberRange {
  from: number
  to: number
}

interface TypedBench {
  ranges: Array<NumberRange>
  unreadable: Array<string>
  count: number
}

// How many numbers a message spells out before it starts counting instead. A
// dozen is enough to go and look at the pots; two hundred is a wall.
const NAMED_IN_A_MESSAGE = 12

// A bench is described the way it is written on the bench card: `81-123`, or
// `81-123, 130`, or one number on its own. An en dash is accepted because a
// phone keyboard and a copied spreadsheet cell both produce one, and `to`
// because that is how a range gets read out loud.
function parseBenchNumbers(text: string): TypedBench {
  const ranges: Array<NumberRange> = []
  const unreadable: Array<string> = []
  for (const part of text.split(',').map((entry) => entry.trim())) {
    if (!part) continue
    const match = /^#?(\d+)(?:\s*(?:-|–|—|to)\s*#?(\d+))?$/.exec(part)
    const from = match ? Number(match[1]) : 0
    const to = match?.[2] === undefined ? from : Number(match[2])
    if (!match || to < from) unreadable.push(part)
    else ranges.push({ from, to })
  }
  return { ranges, unreadable, count: ranges.reduce((running, range) => running + range.to - range.from + 1, 0) }
}

function rangeNumbers(ranges: Array<NumberRange>): Array<number> {
  const numbers = new Set<number>()
  for (const range of ranges) {
    for (let number = range.from; number <= range.to; number += 1) numbers.add(number)
  }
  return [...numbers].sort((first, second) => first - second)
}

function numberList(numbers: Array<number>): string {
  const named = numbers
    .slice(0, NAMED_IN_A_MESSAGE)
    .map((number) => `#${number}`)
    .join(', ')
  return numbers.length > NAMED_IN_A_MESSAGE ? `${named} and ${numbers.length - NAMED_IN_A_MESSAGE} more` : named
}

export { NAMED_IN_A_MESSAGE, NumberRange, TypedBench, numberList, parseBenchNumbers, rangeNumbers }
