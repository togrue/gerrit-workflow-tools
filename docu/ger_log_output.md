Target `ger log` format

Keep the left status columns compact and colored. Move the high-signal, externally-driven state to a quiet trailing label.

Use the trailing attention label only for:

- `submittable`
- `build failed`
- `<n> unresolved comments`
- `abandoned`

Do not add extra trailing labels for lower-signal states like `cr+1`, missing votes, or local/Gerrit drift. Those are already visible in the status columns.

Missing votes should render as `v?` and `cr?`.

Example:

```
99647be2 p v+1 cr-1     # test: cover case 9892           # submittable
4ccb730a p v+1 cr+2     # refactor: reshape blob 9893     # submittable
83b790c4 p v-1 cr+1     # perf: tweak hot path 9894       # build failed
aaf1fe24 p v?  cr?      # wip: experiment 9895
ea2bb4af p v+1 cr?      # wip: experiment 9896
219bee67 p v?  cr-1 com # style: format block 9903        # 2 unresolved comments
```

Color guidance:

- patchset column: green for current, yellow for local ahead, red for outdated, dim for absent/abandoned
- `v+1` and `cr+2`: green
- `cr+1`: light green
- `cr-1`: yellow
- `v-1` and `cr-2`: red
- `com`: yellow
- trailing attention label: green for `submittable`, yellow for unresolved comments, red for failed build or abandoned

When `--url` is present, append the Gerrit web URL inline on the same line:

```
99647be2 p v+1 cr-1     # test: cover case 9892           # submittable https://gerrit.example/c/proj/+/9892
83b790c4 p v-1 cr+1     # perf: tweak hot path 9894       # build failed https://gerrit.example/c/proj/+/9894
```

