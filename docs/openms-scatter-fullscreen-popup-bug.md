# 开源扫雷网「录像」页散点图全屏后下拉列表失效 · 缺陷分析报告

> 分析对象：`eee555/saolei_website`（MIT），`main` 分支 @ `3f4cc34`（2026-10-21）。
> 本地源码快照在 [`_ms/oms_src/`](../_ms/oms_src/)（Git 忽略，不随仓库分发）。
> 前端依赖版本取自 `front_end/package.json`：`element-plus 2.14.7`、`vue-tippy 6.8.0`（tippy.js 6.3.7）。

## 0. 结论

**不是点击事件丢失，也不是 z-index 不够，而是「浏览器全屏只渲染全屏元素子树」与「Element Plus 默认把下拉浮层 teleport 到 `document.body`」叠加造成的。**

三个列表是 `MSStatSelect` 里的 `ElSelect`。它的下拉面板默认被传送到 body 下的全局 popper 容器，而全屏 API 把 `.video-scatter` 单独提进 top layer 后，body 下这个容器既不再绘制、也不参与命中测试，所以看起来「点了没反应」。

## 1. 现象与复现

1. 个人主页 →「录像」页签。
2. 「展示」选「散点图」。
3. 点散点图右上角「全屏」。
4. 点左上角的 x / y / 颜色三个下拉列表：面板不出现，无法选择。

退出全屏后同样三个列表一切正常。说明数据、`v-model` 和点击处理都没有坏，坏的只是浮层的渲染位置。

## 2. 源码链路

### 2.1 页面结构

- [UserVideoView.vue](../_ms/oms_src/front_end_src_views_UserView_UserVideoView.vue#L73-L75)：`presentation === 'scatter'` 时渲染

  ```vue
  <BaseCardNormal>
      <VideoScatter :videos="filteredVideos" />
  </BaseCardNormal>
  ```

- [VideoScatter/App.vue](../_ms/oms_src/front_end_src_components_visualization_VideoScatter_App.vue#L1-L29)：全屏作用在组件根节点上

  ```vue
  <div ref="scatterRef" class="video-scatter" :class="{ 'is-fullscreen': isFullscreen }">
      <Toolbar :is-fullscreen="isFullscreen" @toggle-fullscreen="toggle" />
      ...
  ```

  ```ts
  const scatterRef = useTemplateRef('scatterRef');
  const { isFullscreen, toggle } = useFullscreen(scatterRef);
  ```

  `useFullscreen` 是 `@vueuse/core` 对原生 Fullscreen API 的封装，即 `scatterRef.requestFullscreen()`。

- [Toolbar.vue](../_ms/oms_src/front_end_src_components_visualization_VideoScatter_Toolbar.vue#L5-L9)：左上角三个列表就是三个 `MSStatSelect`

  ```vue
  <MSStatSelect v-model="VideoScatterConfig.x" label="x" :options="[...VideoScatterAxisChoice]" />
  <MSStatSelect v-model="VideoScatterConfig.y" label="y" :options="[...VideoScatterAxisChoice]" />
  <MSStatSelect v-model="VideoScatterConfig.colorBy" :label="t('local.colorBy')" :options="[...VideoScatterColorByChoice]" />
  ```

- [MSStatSelect.vue](../_ms/oms_src/front_end_src_components_Filters_MSStatSelect.vue#L1-L9)：内部就是裸的 `ElSelect`，**没有传 `teleported`**

  ```vue
  <ElSelect v-model="value" size="small">
  ```

### 2.2 Element Plus 把下拉层放到了哪里

`element-plus@2.14.7` 中：

1. `ElSelect` 把 `teleported` 透传给内部 `el-tooltip`（`packages/components/select/src/select.vue`）：

   ```vue
   <el-tooltip ... :teleported="teleported" :append-to="appendTo" ...>
   ```

2. `teleported` 默认值是 **`true`**（`packages/components/tooltip/src/content.ts`）：

   ```ts
   export const useTooltipContentPropsDefaults = {
     ...
     teleported: true,
   }
   ```

3. `ElTooltipContent` 用 Teleport 渲染（`packages/components/tooltip/src/content.vue`）：

   ```vue
   <teleport :disabled="!teleported" :to="appendTo">
   ```

   ```ts
   const appendTo = computed(() => {
     return props.appendTo || selector.value
   })
   ```

4. `selector` 来自 `usePopperContainerId()`，指向一个**由 Element Plus 动态创建并 append 到 `document.body`** 的容器（`packages/hooks/use-popper-container/index.ts`）：

   ```ts
   const createContainer = (id: string) => {
     const container = document.createElement('div')
     container.id = id
     document.body.appendChild(container)
     return container
   }
   ```

所以下拉面板的最终 DOM 位置是：

```
document.body
└── #el-popper-container-xxx     ← 下拉面板在这里
```

而不是：

```
.video-scatter (全屏元素)
└── .video-scatter-toolbar
    └── .el-select              ← 触发按钮在这里
```

### 2.3 全屏时浏览器只渲染哪一块

Fullscreen API 的规范行为是：全屏元素被放进 **top layer**，页面其余内容不参与渲染（不是被盖住，而是不再绘制）。因此：

- 全屏子树 `.video-scatter` 内的触发按钮正常显示、能接收点击；
- body 下的 `#el-popper-container-*` 在子树之外，**完全不绘制，也没有命中区域**；
- 点击触发按钮后，Vue 的 `open` 状态其实已经变成 `true`，但面板没有可见载体，看起来就是「点不开」。

这也解释了为什么调高 `z-index` 没用：问题不在层叠顺序，而在渲染根节点之外。

### 2.4 同类问题不止这三个列表

- [MarkerSetting.vue](../_ms/oms_src/front_end_src_components_visualization_Plots_MarkerSetting.vue#L2) 用 `<Tippy>` 做「点开设置」浮层。tippy.js 6.3.7 的默认 `appendTo` 是 `() => document.body`，全屏下同样会消失。
- [Canvas.vue](../_ms/oms_src/front_end_src_components_visualization_VideoScatter_Canvas.vue#L2) 的散点 hover 提示也是 `Tippy`，同样会失效。

## 3. 修改建议

### 方案 A（不推荐）：关掉 teleport

```vue
<!-- front_end/src/components/Filters/MSStatSelect.vue -->
<ElSelect v-model="value" size="small" :teleported="false">
```

这个改法能让面板在全屏下重新出现，但**实测引入了定位错误**：面板不在触发按钮正下方，而是出现在按钮右侧。

原因：关闭 teleport 后，下拉面板不再是 popper.js 单独挂载的浮层，而成为 `.el-select` 在 flex 工具栏里的子元素，定位包含块和布局上下文都变了。`placement="bottom-start"` 不再按预期相对触发按钮计算，面板被当成工具栏中的后续元素排到右边。

结论：能解决「不可见」，但换来「位置错」，不能单独使用。

### 方案 B（本次采用）：保留 teleport，把挂载目标改到全屏根元素

关键是**不要关闭 teleport**，而是把 teleport 的目标从 `document.body` 换到全屏元素本身。这样面板仍是 popper.js 管理的独立浮层，定位逻辑不变，只是渲染位置落在全屏子树内。

`MSStatSelect` 增加一个 `appendTo` 透传属性，默认 `undefined`（继续使用 Element Plus 的全局容器）：

```vue
<!-- front_end/src/components/Filters/MSStatSelect.vue -->
<ElSelect v-model="value" size="small" :append-to="props.appendTo">
```

```ts
appendTo: {
    type: String,
    default: undefined,
},
```

散点图工具栏的三个下拉在全屏时才指定目标：

```vue
<!-- front_end/src/components/visualization/VideoScatter/Toolbar.vue -->
<MSStatSelect ... :append-to="isFullscreen ? '.video-scatter' : undefined" />
```

这样：

- 全屏时：面板 teleport 到 `.video-scatter` 内，正常绘制、正常点击；
- 非全屏时：`append-to` 为 `undefined`，行为与修改前完全一致，也不会被 `.card` 的 `overflow: auto` 裁剪；
- 只有散点图工具栏使用 `MSStatSelect`，用选择器不会影响其他组件；如果以后需要复用，再改成 provide/inject 传根元素。

#### 方案 B 之后暴露的第二层问题：Popper 误判可用空间，把面板翻到右侧

只加 `append-to` 后，面板能显示、能交互，但位置仍然不对：`data-popper-placement="right"`，面板贴在触发按钮右侧。用系统 Chrome + Puppeteer 在全屏下读到的实测数据：

```
document.fullscreenElement = div.video-scatter.is-fullscreen   (1707 × 904)
trigger  = { x: 130.67, y: 18,  w: 102.67, h: 24, bottom: 42 }
popper   = { x: 245.33, y: 31.33, w: 102.33, h: 275.33 }
placement: "right"
data-popper-reference-hidden: "true"
innerHeight - trigger.bottom - 12 = 850 >= popper.height 275   ← 下方其实完全放得下
```

根因是 popper.js 的裁剪边界（clipping boundary）。默认 `boundary: 'clippingParents'` 会沿 DOM 向上收集所有 `overflow` 不为 `visible` 的祖先作为边界。全屏元素 `position: fixed` 脱离了普通流，它上面这些祖先随之塌缩：

| 祖先 | 测得高度 | overflow |
|---|---|---|
| `.card`（`BaseCardNormal`） | `21.3333px` | `auto` |
| `.el-tabs__content` | `149.99px` | `hidden` |
| `.content-area` / `.common-layout` / `.site-shell` | 同样远小于全屏高度 | 非 `visible` |

于是 `flip` 中间件认为「下方只有 21px 空间，放不下 275px 的面板」，按 `fallbackPlacements` 把 `bottom-start` 翻成 `right`；同时 `data-popper-reference-hidden="true"` —— 这是 popper.js 判定「参照元素已被裁掉」的指纹，正好对上游全屏场景的异常。

修法不是给全屏元素加 `position: relative`（实测无效，placement 仍是 `right`，因为塌缩的是更上层的祖先），而是让 popper 用 viewport 当边界。全屏时给三个下拉传 `popper-options`：

```ts
// front_end/src/components/visualization/VideoScatter/Toolbar.vue
const popperOptions = computed(() => {
    if (!props.isFullscreen) return undefined;
    return {
        modifiers: [
            {
                name: 'flip',
                options: {
                    boundary: 'viewport',
                    fallbackPlacements: ['bottom-start', 'top-start'],
                },
            },
            { name: 'preventOverflow', options: { boundary: 'viewport' } },
        ],
    };
});
```

```vue
<MSStatSelect ... :popper-options="popperOptions" />
```

Element Plus 的 `buildPopperOptions` 会先放内置 modifier，再把用户的追加到后面；popper.js 按 `name` 去重、后者覆盖前者，所以这里的 `boundary: 'viewport'` 生效。非全屏时不传（`undefined`），沿用默认行为。`fallbackPlacements` 只保留上/下两个方向，避免再出现左右翻转。

实测修复后三个下拉均为 `placement = "bottom-start"`、`dx = 0`、`gap = 12`，面板完整落在 viewport 内。

### 方案 C（本次已采用）：Tippy 浮层改为 `append-to="parent"`

`MarkerSetting` 和散点 hover 提示都是 `Tippy`。tippy.js 6.3.7 本身支持 `appendTo: 'parent'`（把浮层挂到参照元素的父节点），`vue-tippy` 的 `Tippy` 组件也把 `appendTo` 作为 prop 原样透传。两处各加一个属性即可，浮层就会留在 `.video-scatter` 全屏子树内：

```vue
<!-- front_end/src/components/visualization/Plots/MarkerSetting.vue -->
<Tippy trigger="click" placement="bottom-start" interactive append-to="parent">
```

```vue
<!-- front_end/src/components/visualization/VideoScatter/Canvas.vue -->
<Tippy class="video-scatter-tippy" :duration="0" sticky follow-cursor append-to="parent">
```

两处的参照元素都是 tippy 自动生成的 `span` 包装：

- `MarkerSetting` 的父节点是 `.video-scatter-toolbar`；
- `Canvas` 的父节点是 `.video-scatter-canvas`。

二者都在全屏子树内，所以浮层不再被 top layer 排除，`follow-cursor` / `sticky` 的定位逻辑也不变。

另外，`MarkerSetting` 弹层内部还有一个形状选择的 `ElSelect`（当前 Toolbar 只传了 `options="radius"`，所以没渲染）。它同样会 teleport 到 body，一旦以后启用 `shape` 就会再踩同一个坑。本次没有改它（未渲染、无法实测行为）；启用时应同样用 `append-to` 指向全屏根元素，**不要**用 `teleported="false"`，否则会重复上面方案 A 的定位问题。

注意不要用 `to="parent"`：在 vue-tippy 里 `to` 改的是 tippy 的**参照元素**，不是浮层挂载点，会把整个交互绑到父节点上。

Element Plus 的 `#el-popper-container-*` 是**全站共享**的，把它整体搬进全屏元素会影响其他浮层，退出时还要搬回来，风险高于逐个组件传 `append-to`，不建议。

### 最终修复清单

| 文件 | 改动 |
|---|---|
| `front_end/src/components/Filters/MSStatSelect.vue` | 新增 `appendTo` / `popperOptions` 两个透传 prop |
| `front_end/src/components/visualization/VideoScatter/Toolbar.vue` | 三个下拉加 `:append-to="isFullscreen ? '.video-scatter' : undefined"` 和 `:popper-options="popperOptions"`（全屏时 boundary 改为 viewport） |
| `front_end/src/components/visualization/Plots/MarkerSetting.vue` | `<Tippy ... append-to="parent">` |
| `front_end/src/components/visualization/VideoScatter/Canvas.vue` | `<Tippy ... append-to="parent">` |

四个文件都是模板/属性级改动，不动逻辑、不动样式，可单独回滚。

### 方案 D（不推荐，除非想要非原生全屏）

不用 Fullscreen API，改成 CSS 固定定位的「伪全屏」：

```less
.video-scatter.is-fullscreen {
    position: fixed;
    inset: 0;
    z-index: 1000; /* Element Plus 浮层默认 2000+，仍在上面 */
}
```

这样文档照常渲染，body 下的浮层不会消失。代价是要自己处理 Esc 退出、滚动锁定、层级和窗口尺寸变化，也失去了原生全屏的多屏/系统语义。除非同时有很多第三方浮层要兼容，否则不建议。

## 4. 验证清单

修复后至少验证：

1. 全屏下依次点开 x / y / 颜色三个下拉，能展开、能选择，面板出现在触发按钮**正下方**而不是右侧，图表即时更新。
2. 下拉面板不被卡片/工具栏裁剪；在 100% / 125% / 150% 缩放和窄窗口下各看一次。
3. 退出全屏后三个下拉仍正常，页面不出现多余滚动条或错位。
4. 键盘操作：聚焦下拉后 Enter/Space 能展开，方向键能选，Esc 能收起（注意 Esc 同时可能触发退出全屏，测一下优先级）。
5. 全屏下点开 `MarkerSetting` 的半径设置面板，能正常出现、能改数值；散点 hover 提示能跟随光标出现，离开后消失。
6. 全屏下把光标移到散点图的边界处，确认 hover 提示没有被 `.card` 或画布裁掉。
7. 回归普通「表格」展示模式与录像页其他筛选器（`SoftwareFilter`、`VideoStateFilter`、模式多选），确认没有因为全局改动受影响。

### 本地实测结果（系统 Chrome + Puppeteer，窗口 1707×904）

验证方式：`vite.localtest.config.ts` 起本地 dev server，`/api` 反代到 `https://openms.top`；用系统 Chrome（`--start-maximized`）打开 `/player/1/videos` 并切到散点图，用 `requestFullscreen` 进入原生全屏后读取 DOM 几何数据。`vue-tsc --noEmit` 与 ESLint 均通过。

| 检查项 | 结果 |
|---|---|
| 非全屏下拉：`placement` | `bottom-start`，`dx = 0`，`gap = 12` |
| 全屏 x 下拉 | `placement = bottom-start`，`dx = 0`，`gap = 12`，面板在全屏子树内，完整落在 viewport |
| 全屏 y 下拉 | 同上 |
| 全屏 颜色 下拉 | 同上 |
| 全屏下点选项 | `x: Bv` → `x: 用时`，图表即时更新 |
| 全屏 MarkerSetting 弹层 | 可见（158×35），挂载在全屏子树内，落在 viewport 内 |
| 全屏散点 hover 提示 | `isShown = true`，父节点 `.video-scatter-canvas`，在 fullscreenElement 子树内，内容已渲染（1379 字符），落在 viewport 内 |
| 退出全屏后下拉 | `placement = bottom-start`，挂回 body 容器，行为与修改前一致 |

截图：`%LOCALAPPDATA%\Temp\mschrome\` 下的 `verify-fullscreen-select-x.png`、`verify-fullscreen-select-colorby.png`、`verify-fullscreen-marker.png`、`verify-fullscreen-hover.png`。

## 5. 影响面与风险

| 方案 | 改动量 | 影响范围 | 主要风险 |
|---|---|---|---|
| A | 1 行 | 仅 `MSStatSelect` | 实测面板跑到触发按钮右侧，定位错误，不采用 |
| B | 2 个 prop + 6 个属性 | 仅散点图三个下拉 | 全屏时依赖 `.video-scatter` 存在；非全屏路径不变，风险低 |
| C | 2 个 Tippy 属性 | 两处 Tippy 浮层 | 依赖父节点仍在全屏子树内；风险低 |
| D | 全屏实现重写 | 整个散点图 | 需自行实现 Esc/滚动/层级，失去原生全屏 |

**本次按 B + C 落地**：四个文件的模板/属性改动，覆盖三个下拉、`MarkerSetting` 弹层和散点 hover 提示；不改全屏实现，也不碰全站共享的 popper 容器。

> 说明：「ElSelect 默认 teleport 到 body + 原生全屏 top layer」这条因果链在 `element-plus 2.14.7` 与 `@vueuse/core useFullscreen` 下成立。方案 A 的定位错误来自本地全屏实测：面板能打开但出现在触发按钮右侧。
