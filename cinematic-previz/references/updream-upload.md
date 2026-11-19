# 本地图片快速上传与复用（0.4.0）

先检查宿主 tools/list 存在 upload_files/list_assets/reuse_assets，浏览器扩展和网页桥接均为0.4.0。源码更新不代表运行服务已更新；维护窗口不为测试重启制作服务、刷新页面或上传实际素材。制作空闲后按原来的接入流程安全重载，重新发现工具。

## 上传

本聊天 open_workspace → claim_canvas，使用真实 tabId/workspaceId/nodeId。`updream_upload_files` 的 files 是用户指定的本地图片绝对路径数组，不是文件内容或目录；最多20张 PNG/JPEG/WebP，单张8MiB、整批32MiB。工具内部读取、校验和编码，不让代理读取大段base64。

- 不传nodeId：只上传到当前项目的普通素材库。
- 传nodeId：上传后将图片放成素材节点，并用isReference=true连入目标节点。已有素材节点和参考连线会复用，保留其他参考；成功需verified和binding.verified均为true。
- 首帧、场景、位置示意图应有明确职责和输入顺序。此接口建立普通参考连线，不配置视频的专用首帧/尾帧槽、音轨或参考编号；返回firstFrameConfigured=false。提交前仍按当前模型实际输入核对。
- 这不是虚拟角色库：白底角色资产的认证入口仍用网站角色流程，不能把含背景的首帧送去做角色认证。

底层只使用网页实时注册的materials.upload/get/addToCanvas和canvas工具，保留网站校验。没有材料工具时返回TOOL_UNAVAILABLE，不猜私有接口，不退回无限点击。网站公开代码确认canvas.updateNode.referenceImages仅支持图片和文本节点，视频不可用此字段；不同站点版本仍以实际Schema和业务返回为准。

## 复用与核对

`updream_list_assets {tabId}` 读取本插件的项目缓存，返回SHA256、assetId、URL和名称，不返回路径/二进制。remoteVerified=false表示缓存不是实时远端列表。

新镜头用`updream_reuse_assets {workspaceId,tabId,nodeId,hashes}`，hashes按意图顺序指定。文件内容相同即使改名也只上传一次；缓存按项目隔离，复用前materials.get核对仍存活且URL一致，不自动重传失效素材。缓存位于插件私有.local/assets，不进入发布包。连线不证明提示词编号、音频、报价和专用首帧已正确。

上传／复用得到画布源节点后，继续按[画布资产节点命名与验收](updream-mcp.md#画布资产节点命名与验收)检查`binding.sources`中的人物、场景等节点，必要时安全改名并读回，将真实名称与nodeId记入现有资产映射。不要把自动沿用的文件名、上传verified或binding.verified当作命名验收；仅上传素材库的情况留到节点实际创建后处理。

## 失败与交接

一次调用不具备事务性；失败读取details.phase/completed/stoppedAt/remaining/currentUploadedReceipt。已成功的素材、节点、连线不回滚。UNKNOWN_OUTCOME或writeMayHaveExecuted时先核对真实素材库/画布，不能直接重跑整批；原有工作区未知结果锁仍有效。当前项有回执但读回失败时保留assetId/URL用于核对，不能将其当作已验证完成。

本地/隔离链路测试只证明代码契约；真实网站上传、素材尺寸限制、模型参考顺序仍需现场验收。不宣称上传速度提升百分比，不用真实素材做维护测试。
