# Policy corpus

Synthetic policies for a fictional task marketplace. Each file is one immutable version:
YAML front matter (identity, effective period, structured parameters) followed by numbered
sections. Section `4.3` of payout version 12 becomes passage `payout_v12_section_4_3`.

Parameters are the values deterministic checks use. Each names the section that states it,
so a check and its citation always point at the same text. Changing a loaded version is
refused; publish a new version instead. Publishing a new version also sets the end date of the
open one. The loader accepts that single change, and only when the end date is not in the past,
because ending a version retroactively would change the rules for cases already decided.
