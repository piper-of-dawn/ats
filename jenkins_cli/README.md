# Jenkins CLI

This folder now contains a thin wrapper around `jenkins-cli.jar`.

## Usage

The wrapper loads Jenkins settings from the repo `.env` file automatically.

If you want to use the official Jenkins CLI pattern:

```bash
./jenkins_cli/cli help
```

By default it stores the jar here:

```text
/tmp/jenkins-cli.jar
```

On first run, the wrapper downloads:

```text
${JENKINS_URL}/jnlpJars/jenkins-cli.jar
```

You can override the jar path with:

```bash
export JENKINS_CLI_JAR=/path/to/jenkins-cli.jar
```

The wrapper reads these variables from `.env`:

```bash
JENKINS_URL="https://jenkins.kumarshantanu.com"
JENKINS_USER="admin"
JENKINS_TOKEN="..."
```

## Examples

```bash
./jenkins_cli/cli help
./jenkins_cli/cli version
./jenkins_cli/cli who-am-i
./jenkins_cli/cli list-jobs
./jenkins_cli/cli build ATS/daily-run -s
```

If you want to bypass `.env`, you can still override values in the shell:

```bash
JENKINS_USER="other-user" ./jenkins_cli/cli who-am-i
```
