// CI/CD pipeline for kazemo.
//
// CI : every commit -> build test image -> lint -> unit tests (JUnit + coverage) -> build runtime image -> smoke test
// CD : optional push to Docker Hub, deployment of the image to a staging container and/or Minikube.
//
// Requirements on the Jenkins controller: Docker CLI with access to a Docker daemon (see jenkins/),
// kubectl only for the Kubernetes stage. Everything else runs inside containers, so the agent
// needs no Python installation.

pipeline {
    agent any

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
    }

    // A Jenkins on a laptop cannot receive GitHub webhooks, so it polls the repository every
    // ~2 minutes: each new commit starts a build. With a public Jenkins use a webhook instead.
    triggers {
        pollSCM('H/2 * * * *')
    }

    parameters {
        string(name: 'DOCKER_REPO', defaultValue: 'tenajuro12/kazemo', description: 'Docker Hub repository for the image')
        booleanParam(name: 'PUSH_IMAGE', defaultValue: false, description: 'Push the image to Docker Hub (credential "dockerhub")')
        booleanParam(name: 'DEPLOY_STAGING', defaultValue: true, description: 'Run the new image as the staging container on port 8090')
        booleanParam(name: 'DEPLOY_K8S', defaultValue: false, description: 'Deploy to Minikube (credential "kubeconfig"; image must be pushed or loaded)')
    }

    environment {
        IMAGE = "${params.DOCKER_REPO}"
        DOCKER_BUILDKIT = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                script {
                    env.TAG = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
                    env.REVISION = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                }
                sh 'git log -1 --format="%h %an: %s"'
            }
        }

        stage('Build test image') {
            steps {
                sh 'docker build --target test -t kazemo-test:$TAG .'
            }
        }

        stage('Lint') {
            steps {
                sh 'docker run --rm kazemo-test:$TAG sh -c "ruff check . && ruff format --check ."'
            }
        }

        stage('Unit tests') {
            steps {
                sh '''
                    rm -rf reports && mkdir -p reports
                    docker rm -f kazemo-test-$TAG >/dev/null 2>&1 || true
                    set +e
                    docker run --name kazemo-test-$TAG kazemo-test:$TAG \
                        pytest -q --junitxml=/tmp/reports/junit.xml \
                               --cov-report=xml:/tmp/reports/coverage.xml --cov-fail-under=90
                    status=$?
                    set -e
                    docker cp kazemo-test-$TAG:/tmp/reports/. reports/ || true
                    docker rm kazemo-test-$TAG >/dev/null
                    exit $status
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Build image') {
            steps {
                sh '''
                    docker build \
                        --build-arg VERSION=$TAG --build-arg REVISION=$REVISION \
                        -t $IMAGE:$TAG -t $IMAGE:latest .
                    docker image ls $IMAGE
                '''
            }
        }

        stage('Smoke test image') {
            steps {
                sh '''
                    docker run --rm $IMAGE:$TAG --version
                    docker run --rm --entrypoint sh $IMAGE:$TAG -c '
                        cp /app/examples/demo_raw.jsonl /data/raw.jsonl &&
                        kazemo run /app/deploy/demo.toml --skip-collect > /dev/null &&
                        test -s /data/dashboard.html &&
                        kazemo stats /data/clean.jsonl'
                '''
            }
        }

        stage('Push image') {
            when { expression { params.PUSH_IMAGE } }
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub', usernameVariable: 'DH_USER', passwordVariable: 'DH_TOKEN')]) {
                    sh '''
                        echo "$DH_TOKEN" | docker login -u "$DH_USER" --password-stdin
                        docker push $IMAGE:$TAG
                        docker push $IMAGE:latest
                    '''
                }
            }
            post {
                always { sh 'docker logout || true' }
            }
        }

        stage('Deploy to staging') {
            when { expression { params.DEPLOY_STAGING } }
            steps {
                sh '''
                    docker rm -f kazemo-staging >/dev/null 2>&1 || true
                    docker run -d --name kazemo-staging --restart unless-stopped \
                        -p 8090:8000 --entrypoint /app/deploy/demo.sh $IMAGE:$TAG
                    for i in $(seq 1 30); do
                        if docker exec kazemo-staging python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz')" 2>/dev/null; then
                            echo "Staging is up: http://localhost:8090/ (image $IMAGE:$TAG)"
                            exit 0
                        fi
                        sleep 2
                    done
                    docker logs kazemo-staging
                    exit 1
                '''
            }
        }

        stage('Deploy to Kubernetes') {
            when { expression { params.DEPLOY_K8S } }
            steps {
                withCredentials([file(credentialsId: 'kubeconfig', variable: 'KUBECONFIG')]) {
                    sh '''
                        kubectl apply -f deploy/k8s/
                        kubectl set image deployment/kazemo kazemo=$IMAGE:$TAG
                        kubectl rollout status deployment/kazemo --timeout=180s
                        kubectl get pods,svc -l app=kazemo -o wide
                    '''
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
            sh 'docker rmi kazemo-test:$TAG >/dev/null 2>&1 || true; docker image prune -f >/dev/null || true'
        }
        success {
            echo "Built ${env.IMAGE}:${env.TAG}"
        }
    }
}
