# KEO实验Neo4j Community部署

当前仅准备部署文件，尚未启动容器、创建数据库或导入数据。现有业务代码和对外接口不变。

## 方案

- Docker Desktop的Linux容器模式，独立Compose项目keo-graph-rag。
- 镜像neo4j:5.26，为Community Edition的5.26 LTS系列；该标签可能随补丁更新，首次拉取后记录实际digest，正式实验固定该镜像。
- Community Edition只能有一个业务数据库；本实例使用neo4j，与业务图谱使用不同实例，不在原实例尝试新建keo数据库。
- 浏览器地址：http://localhost:7475；Python连接：bolt://localhost:7688。
- 用户名neo4j，数据库neo4j，密码由部署目录.env设置。
- 仅绑定本机地址；当前方案面向本机开发，不包含远程访问配置。
- 数据和日志使用独立Docker命名卷，不挂载原业务目录。命名卷位于Docker管理的存储中，不一定在项目D盘，需检查Docker Desktop磁盘位置和剩余空间。

## 1. 启动前准备

打开Docker Desktop，确认Linux引擎运行。执行：

```powershell
docker info
```

当前小样本配置：容器上限4GB、heap上限1GB、page cache 1GB、2 CPU。Docker引擎可用内存应大于容器上限，并为Windows、Python和Chroma留出空间。尚未测得本机可用总内存，不能据此直接进行百万三元组导入。

以下命令均在本目录执行：

```powershell
Set-Location 'D:\Multi-modal-Police-Intelligence-Analysis\experiments\KEO\deploy\neo4j'
Copy-Item -LiteralPath .env.example -Destination .env
```

只在首次初始化时复制，不覆盖已有.env。编辑新建的.env，将示例密码换成独立实验库密码，不复用业务密码。

所有Compose命令显式使用--env-file .env，避免读取项目根目录的.env。不要使用普通config命令打印完整配置，它会展开密码；验证使用config --quiet。

## 2. 拉取和启动

```powershell
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml pull
docker image inspect neo4j:5.26 --format '{{json .RepoDigests}}'
docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail 80 neo4j
```

记录镜像digest。正式实验可将compose.yaml的image替换为neo4j@sha256:实际digest，以固定版本；已有数据库不要自动切换镜像版本。

等待日志显示Bolt与HTTP服务启动，再打开http://localhost:7475。若Browser需要填写连接地址，使用bolt://localhost:7688，用户名neo4j及刚设置的密码。

## 3. 验证空实验库

登录Browser后执行：

```cypher
RETURN 1 AS connected;
CALL dbms.components() YIELD name, versions, edition
RETURN name, versions, edition;
MATCH (n) RETURN count(n) AS node_count;
```

预期连接成功、edition为community，新实验库node_count为0。容器显示running并不等于数据库已准备好。此阶段不导入业务数据；约束随后由neo4j_store.py创建。

## 4. 后续Python连接配置

后续配置类使用以下值；此处只是说明，不自动修改项目根目录.env：

```dotenv
KEO_NEO4J_URI=bolt://localhost:7688
KEO_NEO4J_USER=neo4j
KEO_NEO4J_PASSWORD=填写同一个实验库密码
KEO_NEO4J_DATABASE=neo4j
```

部署目录.env供Compose使用，之前提供的Python配置类读取项目根目录.env，两者不会自动同步。真正联调前要保证密码一致、根目录.env采用合法KEY=VALUE格式。不要把PowerShell的$env:变量赋值语句直接放进dotenv文件。本次未修改该文件。

requirements.txt已有neo4j==6.2.0，不需要为这一步另装Python驱动。Chroma仍由后续节点索引代码维护，不在本Compose中部署。

## 5. 停止与持久化

```powershell
docker compose --env-file .env -f compose.yaml stop
docker compose --env-file .env -f compose.yaml start
```

stop/start保留数据。不要执行down -v，该操作会删除本项目数据卷。命名卷提供持久化，不等同于备份；正式全量导入前应另外安排停止数据库后的离线dump与恢复验证。

NEO4J_AUTH只初始化空数据卷的密码。已有数据后修改.env不会自动修改数据库密码，须按Neo4j密码变更流程操作，不应通过删除卷解决。

## 6. 进入全量导入前

先导入少量build_ids，测试幂等重跑、共享节点来源追溯、删除、证据去重和向量同步，再按逐步扩大的样本测量内存、磁盘、写入与查询耗时。当前事实节点方案会使物理节点/关系数高于原始三元组数，不能只按100万条估算容量。

确认Python只连接7688端口；eval_ids不导入。Neo4j始终持久化全图，NetworkX仅接收有限查询子图，graph.json不作为正式启动依赖。

## 官方依据

- [Community数据库数量限制](https://neo4j.com/docs/operations-manual/current/database-administration/)
- [官方Docker镜像、持久化与初始密码](https://neo4j.com/docs/operations-manual/current/docker/introduction/)
- [Docker配置与内存环境变量](https://neo4j.com/docs/operations-manual/current/docker/configuration/)
- [5.26 LTS介绍](https://neo4j.com/blog/developer/neo4j-v5-lts-evolution/)
