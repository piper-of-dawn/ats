def repoUrl = System.getenv('ATS_REPO_URL') ?: 'https://github.com/your-org/ats_python.git'
def repoCredentialsId = System.getenv('ATS_REPO_CREDENTIALS_ID') ?: ''
def buildBranch = System.getenv('ATS_BUILD_BRANCH') ?: 'main'

folder('ATS') {
    displayName('ATS')
    description('ATS pipelines managed by Jenkins Configuration as Code')
}

multibranchPipelineJob('ATS/build') {
    displayName('ATS Build')
    description('Builds the ATS Docker image from the repository Jenkinsfile')
    branchSources {
        git {
            id('ats-build-main')
            remote(repoUrl)
            includes(buildBranch)
            if (repoCredentialsId) {
                credentialsId(repoCredentialsId)
            }
        }
    }
    orphanedItemStrategy {
        discardOldItems {
            numToKeep(10)
        }
    }
}

pipelineJob('ATS/daily-run') {
    displayName('ATS Daily Run')
    description('Runs the scheduled ATS batch commands using jenkins/Jenkinsfile.run')
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url(repoUrl)
                        if (repoCredentialsId) {
                            credentials(repoCredentialsId)
                        }
                    }
                    branch(buildBranch)
                }
            }
            scriptPath('jenkins/Jenkinsfile.run')
            lightweight(true)
        }
    }
}

pipelineJob('ATS/gmail-sync') {
    displayName('ATS Gmail Sync')
    description('Runs the scheduled Trading 212 Gmail sync using jenkins/Jenkinsfile.gmail')
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url(repoUrl)
                        if (repoCredentialsId) {
                            credentials(repoCredentialsId)
                        }
                    }
                    branch(buildBranch)
                }
            }
            scriptPath('jenkins/Jenkinsfile.gmail')
            lightweight(true)
        }
    }
}
