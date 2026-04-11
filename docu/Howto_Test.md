I've set up a test repository at `/d/projects/external/workflow-optimization/test-git-graph-repo`.

Install the `gerrit-workflow-tools` package from this workspace (invoked with `PYTHONPATH=<this-repo>/src python -m gerrit_workflow_tools.cli_<name>` so the current working directory stays the test repo).

Then change to the test repository and run the command that should be tested.

Look at the output of the command and compare it to the expected output.



