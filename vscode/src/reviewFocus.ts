// The chain a staged review here is about: Review chains narrows to it, and the Change view's Next
// knows from it whether the chain goes on above the change reviewed. Pure: no `vscode`.

import { Chain, Member, Overview, Section, Stack } from "./api";

/** The chain a staged review here holds a change of, as Review chains has it, and that change's number. */
export interface ReviewFocus {
  chain: Chain;
  section: Section;
  number: number;
}

/** The chain of the change a staged review holds in this checkout (core's `staged_review`), found in the inbox. */
export function reviewFocus(overview: Overview | undefined, stack: Stack | undefined): ReviewFocus | undefined {
  const number = stack?.staged_review?.number;
  for (const section of number ? (overview?.sections ?? []) : []) {
    const chain = section.chains.find((c) => c.members.some((member) => member.number === number));
    if (chain && number) {
      return { chain, section, number };
    }
  }
  return undefined;
}

/** The member the focus is on, and whether the chain goes on above it (base first, as core orders members). */
export function focusedMember(focus: ReviewFocus): { member: Member | undefined; above: boolean } {
  const at = focus.chain.members.findIndex((member) => member.number === focus.number);
  return { member: focus.chain.members[at], above: at >= 0 && at < focus.chain.members.length - 1 };
}
