
## Ger show issue

On gerrit this is shown (i just copied the text from the gerrit web UI).

```
Tobias Grün
Patchset 1
Apr 13
Could you change the style?

Tobias Grün
Patchset 1
3:10 PM
Another Comment

Tobias Grün
Patchset 1
3:10 PM
Oh now it is resolved

Resolved
```

As you can see the overall comment chain is resolved.

## Current behavior

ger show I30abcfccf847566b5df3e90c1138cd83571717f5

    http://lenovo-pc:8080/c/test-git-graph-repo/+/15
    ef363a8e p v?  cr-1     # style: format block 9903

Unresolved comments:
  /PATCHSET_LEVEL  grt (Tobias Grün)
  url: http://lenovo-pc:8080/c/test-git-graph-repo/+/15/comment/6d9478f2_d9bc4a2f/
    Could you change the style?

  /PATCHSET_LEVEL  grt (Tobias Grün)
  url: http://lenovo-pc:8080/c/test-git-graph-repo/+/15/comment/448d2fb4_315d1653/
    Another Comment

## What should be different:

* The git commit message should be shown. (the same way as it is shown when running `ger show` with a git commit hash)
* The comments view in the example above should show this:
Unresolved comments:
  (no unresolved comments)

* Comment chains should be formatted like this:

  /PATCHSET_LEVEL  http://lenovo-pc:8080/c/test-git-graph-repo/+/15/comment/6d9478f2_d9bc4a2f/
    grt (Tobias Grün)
      Could you change the style?

    grt (Tobias Grün) - (2 days ago)
      Another Comment

    grt (Tobias Grün) - (1 day ago)
      Please run the style checker

  epsilon.txt:873  http://lenovo-pc:8080/c/test-git-graph-repo/+/15/comment/c2d6bdcf_89e2fee3/
    grt (Tobias Grün) - (10 minutes ago)
      Some comment with a source location

## How it should work

Ger show builds a internal representation of the comment chains. When the last comment is resolved, the whole chain is resolved.
We want this to happen in shared code. So there should be an api to just get the comment chains for a given change.

Criteria:
* A comment chain is resolved when the last comment in this chain is resolved.


