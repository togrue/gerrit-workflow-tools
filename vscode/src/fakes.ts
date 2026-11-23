// What a report puts in the place of something from the repository or Gerrit (src/anonymize.ts):
// filler words, made-up names, path segments, commit ids and numbers. All of it is made up from dice
// and a word list, never computed from the original, so nothing can be guessed back from it; only
// the length (for a path segment, roughly) and the shape are the original's. One Names is one
// report: the same original gets the same replacement, from tables that live as long as it does.
// Pure: no `vscode` import.

/** A whole number from 0 up to, not including, `below`. Tests pass a seeded one. */
export type Random = (below: number) => number;

const FILLER = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure in reprehenderit voluptate velit esse cillum fugiat nulla pariatur excepteur sint occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est".split(" ");
const CONSONANTS = "bdfgklmnprstvz";
const VOWELS = "aeiou";
const HEX = "0123456789abcdef";

/** A path segment's length, blurred: an exact length would help to confirm a guessed name. */
function roughly(length: number): number {
  const step = length <= 8 ? 2 : length <= 24 ? 4 : 8;
  return Math.ceil(length / step) * step;
}

/** `length` characters of words with single blanks between them; the words come from `lengths` and `make`, not from any original. */
function fit(length: number, lengths: () => number, make: (length: number) => string): string {
  const words: string[] = [];
  for (let left = length; left > 0; ) {
    let size = Math.min(lengths(), left);
    if (left - size === 1) {
      size = left; // no room for a blank and another word: this one takes the rest
    }
    words.push(make(size));
    left -= size + 1;
  }
  return words.join(" ");
}

/** The replacements of one report: what each original became, so it becomes that again. Never kept, never derived from the original. */
export class Names {
  private readonly tables = new Map<string, Map<string, string>>();
  private fillerAt = 0;
  private count = 0;

  constructor(private readonly random: Random) {}

  private once(kind: string, original: string, make: () => string): string {
    const table = this.tables.get(kind) ?? new Map<string, string>();
    this.tables.set(kind, table);
    const known = table.get(original);
    if (known !== undefined) {
      return known;
    }
    const made = make();
    table.set(original, made);
    return made;
  }

  private letters(length: number, alphabet: string): string {
    return Array.from({ length }, () => alphabet[this.random(alphabet.length)]).join("");
  }

  /** A made-up word of `length` letters. */
  private word(length: number): string {
    const start = this.random(2);
    return Array.from({ length }, (_, index) => this.letters(1, (index + start) % 2 ? VOWELS : CONSONANTS)).join("");
  }

  /** Filler with the length of every line of `text`, and its line breaks. */
  text(text: string): string {
    const filler = (length: number) => {
      const word = FILLER[this.fillerAt++ % FILLER.length];
      return (word + word + word).slice(0, length).padEnd(length, "m");
    };
    const line = (original: string) => this.once("line", original, () => fit(original.length, () => FILLER[this.fillerAt % FILLER.length].length, filler));
    return text.split("\n").map(line).join("\n");
  }

  /** A made-up name as long as `name` (an e-mail address in it goes too). */
  person(name: string): string {
    const made = (length: number) => {
      const word = this.word(length);
      return word[0].toUpperCase() + word.slice(1);
    };
    return this.once("person", name, () => fit(name.length, () => 4 + this.random(4), made));
  }

  /** A path with as many segments, each made up and roughly as long; the same folder is the same folder again. No name, no extension. */
  path(path: string): string {
    let parent = "";
    return path
      .split(/([\\/])/)
      .map((part) => {
        if (part === "" || part === "/" || part === "\\") {
          return part;
        }
        const taken = new Set(this.tables.get(`in ${parent}`)?.values());
        const made = this.once(`in ${parent}`, part, () => {
          let word = this.word(roughly(part.length));
          for (let tries = 0; taken.has(word) && tries < 20; tries++) {
            word = this.word(roughly(part.length));
          }
          return word;
        });
        this.once("name", part, () => made);
        parent = `${parent}/${part}`;
        return made;
      })
      .join("");
  }

  /** A file's name standing alone: what it became in a path, when one had it. */
  fileName(name: string): string {
    return this.tables.get("name")?.get(name) ?? this.path(name);
  }

  /** Hex digits, as many: a commit id, whole or short. A short id is the start of what its whole id became. */
  hex(original: string): string {
    const table = this.tables.get("hex");
    if (table && !table.has(original) && /^[0-9a-f]{4,}$/.test(original)) {
      for (const [known, made] of table) {
        if (known.startsWith(original)) {
          return this.once("hex", original, () => made.slice(0, original.length));
        }
        if (/^[0-9a-f]{4,}$/.test(known) && original.startsWith(known)) {
          return this.once("hex", original, () => made + this.letters(original.length - known.length, HEX));
        }
      }
    }
    return this.once("hex", original, () => {
      let made = this.letters(original.length, HEX);
      while (made === original && made.length) {
        made = this.letters(original.length, HEX);
      }
      return made;
    });
  }

  /** `I` and hex digits. */
  changeId(original: string): string {
    return `I${this.hex(original.replace(/^I/, ""))}`;
  }

  /** What a view calls a change by: its Change-Id, else its commit. */
  key(original: string): string {
    return /^I./.test(original) ? this.changeId(original) : this.hex(original);
  }

  /** Another number with as many digits: a change number, a line. */
  number(original: string): string {
    return this.once("number", original, () => {
      let made = original;
      while (made === original) {
        made = `${1 + this.random(9)}${this.letters(Math.max(0, original.length - 1), "0123456789")}`;
      }
      return made;
    });
  }

  url(original: string): string {
    return this.once("url", original, () => `https://example.invalid/${++this.count}`);
  }

  /** An id nothing is read from: a comment chain's. */
  id(original: string): string {
    return this.once("id", original, () => `id-${++this.count}`);
  }
}
