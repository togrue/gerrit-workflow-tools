// Demo project job: verify every new Gerrit patchset of ci-demo.
job('ci-demo') {
    description('Runs the ci-demo tests for each Gerrit patchset and votes Verified.')
    scm {
        git {
            remote {
                url("ssh://jenkins@${System.getenv('GERRIT_INTERNAL_HOST')}:${System.getenv('GERRIT_SSH_PORT')}/ci-demo")
                credentials('gerrit-ssh')
                refspec('$GERRIT_REFSPEC')
            }
            branch('$GERRIT_PATCHSET_REVISION')
        }
    }
    triggers {
        gerrit {
            events {
                patchsetCreated()
            }
            project('plain:ci-demo', ['ant:**'])
        }
    }
    // A "recheck" comment re-runs CI on that patchset (Job DSL has no helper for this event).
    configure { project ->
        def events = project / triggers / 'com.sonyericsson.hudson.plugins.gerrit.trigger.hudsontrigger.GerritTrigger' / triggerOnEvents
        events << 'com.sonyericsson.hudson.plugins.gerrit.trigger.hudsontrigger.events.PluginCommentAddedContainsEvent' {
            commentAddedCommentContains('^recheck$')
        }
    }
    steps { shell('python3 -m pytest -q --junitxml=report.xml') }
    publishers { archiveJunit('report.xml') }
}
