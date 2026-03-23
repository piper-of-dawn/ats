# Jenkins Configuration as Code

This directory contains a minimal Jenkins Configuration as Code setup for this repo.

## What it does

- Configures a local Jenkins admin user.
- Sets the Jenkins URL and admin email.
- Exposes a few global environment defaults used by the ATS jobs.
- Creates three Jenkins jobs from code:
  - `ATS/build`
  - `ATS/daily-run`
  - `ATS/gmail-sync`

## What it does not do

- It does not install Jenkins plugins.
- It does not create Gmail file credentials for you.
- It does not provision Docker permissions on the Jenkins host.

## Required plugins

Install these before applying this config:

- `configuration-as-code`
- `job-dsl`
- `workflow-aggregator`
- `git`
- `credentials`
- `credentials-binding`
- `branch-api`
- `cloudbees-folder`

## Environment variables

Set these on the Jenkins controller before startup:

- `CASC_JENKINS_CONFIG=/var/jenkins_home/casc`
- `JENKINS_ADMIN_USERNAME=admin`
- `JENKINS_ADMIN_PASSWORD=...`
- `JENKINS_ADMIN_EMAIL=...`
- `JENKINS_URL=https://jenkins.example.com/`
- `ATS_REPO_URL=<git repo url>`

Optional:

- `ATS_REPO_CREDENTIALS_ID=<jenkins git credential id>`
- `ATS_BUILD_BRANCH=main`
- `ATS_BUILD_STATE_DIR=/var/lib/jenkins/ats-build`
- `ATS_APP_ENV_FILE=/var/lib/jenkins/ats/app.env`
- `ATS_GMAIL_RUNTIME_DIR=/var/lib/jenkins/ats-gmail`

## File layout on the Jenkins controller

Copy this repo folder into Jenkins so it is available at:

```text
/var/jenkins_home/casc/jenkins.yaml
/var/jenkins_home/casc/jobs/ats-jobs.groovy
```

Then set:

```bash
export CASC_JENKINS_CONFIG=/var/jenkins_home/casc
```

## Important note about credentials

`jenkins/Jenkinsfile.gmail` expects Jenkins file credentials with these IDs:

- `gmail-oauth-client-secret`
- optional bootstrap token credential ID you pass as `GMAIL_TOKEN_BOOTSTRAP_CREDENTIAL_ID`

The exact YAML shape for file credentials depends on the installed credentials plugins and is easiest to get by:

1. Creating the credential once in the Jenkins UI.
2. Opening `Manage Jenkins -> Configuration as Code -> View Configuration`.
3. Copying the exported credential YAML into your managed JCasC config.

That export is the safest source of truth for your controller.
