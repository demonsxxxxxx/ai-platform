# s75 Docker 到同机 OpenSandbox 迁移

本文是 `release-operations-runbook.md` 下的一次性迁移步骤，只适用于已经由
Issue #1278 审核确认的旧部署：

- 源 Compose project 是 `ai-platform-internal`；
- 源 contour 是 `docker-compose.yml + docker-compose.sandbox.yml`；
- 目标 project 是 `ai-platform-phaseb`；
- 目标 contour 是 `docker-compose.yml + docker-compose.s72-colocation.yml + docker-compose.s75-migration.yml`。

其他项目名、Compose 组合、volume 名称或主机不得套用本流程。普通版本更新继续使用
release runbook，不再运行本迁移命令。

## 1. 前置门禁

开始前必须同时满足：

1. Stage A 和 Stage B 已合并，Backend、Frontend、Packaging 和 release manifest
   对同一个完整 commit 全部通过；Backend/Frontend 使用 manifest 绑定的 immutable digest。
2. 目标和回滚 checkout 都是对应完整 commit 的 clean tree。
3. `opensandbox.service` 已按 OpenSandbox 官方包安装，root-owned、active，且本机
   `http://127.0.0.1:8080/health` 返回 200。
4. `opensandbox-gateway.service` 已按
   `s72-opensandbox-gateway-runbook.md` 的 host-colocation 规则安装并 active；安装时使用
   本机 machine-id SHA-256，不打印 secret。
5. `runsc` 已注册；目标 executor image digest 已在该 Docker host 缓存或可拉取。
6. managed environment file 是 root-owned regular file、非 symlink、mode `0600`。
7. s75 没有 active Run、RunAttempt、sandbox lease、Docker sandbox 或 native-tool container。
8. 当前八个 legacy Compose 服务同属一个 clean 历史 release，以下 volume mount identity
   与 Docker Compose labels 完全匹配：

```text
ai-platform-internal_ai_platform_postgres
ai-platform-internal_ai_platform_redis
ai-platform-internal_ai_platform_minio
ai-platform-internal_ai_platform_sandbox_workspaces
```

不手工改 volume label，不复制数据，不创建同名空 volume。任何一项不符都停止。

## 2. 执行迁移

通过一个独立的 root systemd oneshot 单元运行，确保 SSH 断开不会终止进程。单元的
`ExecStart` 只调用下列命令；代理若需要，只注入该单元，不写入 Git 或仓库配置。

```bash
sudo -n /usr/bin/python3 -m tools.s75_opensandbox_transition migrate \
  --target-repo-root "$TARGET_REPO_ROOT" \
  --target-commit "$TARGET_COMMIT" \
  --legacy-repo-root "$LEGACY_REPO_ROOT" \
  --legacy-commit "$LEGACY_COMMIT" \
  --env-file "$MANAGED_ENV_FILE" \
  --backend-image "$IMMUTABLE_BACKEND_IMAGE" \
  --frontend-image "$IMMUTABLE_FRONTEND_IMAGE" \
  --docker-cmd "docker"
```

该命令按固定顺序执行：

1. 取得 root-owned POSIX flock；
2. 验证 legacy ownership、四个 volume、host services、零 active work；
3. 在停机前 pull、验证并本地 tag 两个 CI immutable image；
4. 对目标三文件 Compose 做 semantic preflight；
5. 停止旧 API/Worker admission，再次检查 DB、lease 和 sandbox container；
6. 对旧项目执行 `down --remove-orphans`，不使用 `-v`；
7. 只通过 release authority 启动目标 project；
8. 验证 target parity 后输出 JSON 结果和明确 rollback image references。

命令不读取或打印 `.env` 内容。源和目标 project 不会同时挂载这些 writable volumes。

如果 target deployment 失败，命令自动对目标执行不带 `-v` 的 `down`，再用迁移前
观察到的 exact legacy image 和 Compose selection 恢复旧项目。若自动 rollback 也失败，
保持 mutation lease，不运行第二个部署命令，先检查该 systemd unit 的固定错误类别和
Docker service state。

## 3. 显式回滚

在开放普通流量前发现问题，且再次确认零 active work 后，使用迁移结果中的三个
legacy image reference 执行：

```bash
sudo -n /usr/bin/python3 -m tools.s75_opensandbox_transition rollback \
  --target-repo-root "$TARGET_REPO_ROOT" \
  --target-commit "$TARGET_COMMIT" \
  --legacy-repo-root "$LEGACY_REPO_ROOT" \
  --legacy-commit "$LEGACY_COMMIT" \
  --env-file "$MANAGED_ENV_FILE" \
  --legacy-backend-image "$LEGACY_BACKEND_IMAGE" \
  --legacy-frontend-image "$LEGACY_FRONTEND_IMAGE" \
  --legacy-executor-image "$LEGACY_EXECUTOR_IMAGE" \
  --docker-cmd "docker"
```

回滚复用同四个 external volumes，不使用 `down -v`。如果 legacy 启动失败，authority
会清除 partial legacy containers 并恢复 target；两边都无法恢复时停止操作并保留现场。

镜像/provider 回滚不等于数据库降级。若目标 release 已执行旧镜像不兼容的 schema
migration，不得声称 legacy image rollback 可用；必须按 release runbook 的 schema
兼容边界处理。

## 4. 上线验收

迁移命令成功只证明 deployment parity，不证明 OpenSandbox lifecycle。开放普通任务前，
在 exact target runtime 上记录以下 redacted evidence：

- API health/readiness 与 schema current；
- API、Worker、Frontend exact commit/image，restart count 为 0；
- PostgreSQL、Redis、MinIO healthy，四个 mount identity 与迁移前一致；
- `opensandbox.service`、gateway/broker health；
- disposable sandbox create、command、file write/read、stop；
- observed OCI runtime 为 `runsc`，network mode 为 `none`；
- stop 后无 orphan sandbox/container/lease，Worker 稳定窗口无新错误。

任一项失败时，不接收普通任务；仍在兼容回滚窗口内则执行第 3 节，否则按 incident
流程保留证据并停止。
