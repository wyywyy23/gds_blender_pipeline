# 显微镜下芯片轻微虹彩：可行性评估

日期：2026-09-18。基线：已公开推送的 `3c0761d`。
状态：可行性判断与待验证方案；尚未选择或实现新的默认材质。

## 判断

可以实现接近显微镜观感的轻微虹彩。优先研究**中性白光照明 + 低强度、随角度变化的干涉反射**。灯光决定哪些反射进入相机；材料或显微成像机制决定不同波长的相对强弱。仅调整当前白色灯光的位置、大小和强度，不会自动给现有中性玻璃增加薄膜干涉规律。

用户希望继续保留：可见的 cladding 和 undercut、清楚的下层器件、较少重影、中性玻璃质感；共享材料适用于所有 GDS。虹彩应来自反射变化，不能重新变成有色半透明体。

还没有实际显微照片、照明模式或光学膜层参数，因此不能断定用户看到的颜色来自哪一种机制。

## 三种需要区分的来源

| 可能来源 | 识别线索 | 对当前渲染的意义 |
| --- | --- | --- |
| 薄膜干涉 | 氧化物/氮化物覆盖区域随膜厚、观察角度呈现不同色相 | 最适合先试的方向；BYU 的 SiO₂/SiN-on-Si 工具直接说明了膜厚与角度依赖。[1] |
| 周期结构衍射 | 彩色集中在密集周期图形，旋转样品或改变照明方向时明显变化 | 需与周期、方向和波长相关的衍射响应；不能把所有 cladding 都赋予同一种“光栅颜色”。白光的不同波长有不同衍射角。[2] |
| 反射式 DIC 成像 | 边缘出现窄彩纹或浮雕感，并使用 DIC/偏振相关设置 | Nikon 说明白光反射 DIC 能在表面特征附近产生彩色条纹。这属于成像系统效果，不能仅归因于玻璃 IOR。[3] |

“轻微七彩”不一定意味着同一平坦均匀区域同时铺满彩虹。均匀膜厚与接近一致的视角通常不会凭空产生随机彩斑；空间变化应有膜厚、表面朝向、图形或成像条件的依据。

## 与当前实现的关系

本地能力检查确认 Blender **5.2.2 LTS** 的 Principled BSDF 和 Metallic BSDF 均有 `Thin Film Thickness` 与 `Thin Film IOR` 输入。Blender 文档将薄膜颜色与视角、膜厚和两侧折射率联系起来；膜厚单位是 **nm**，零厚度关闭效果。[4][5]

当前 `apply_cladding_presentation_glass()` 的顶面走 Transparent + Glossy 分支，Principled 只用于真实孔壁。因此只在原 Principled 节点填 Thin Film 参数，会主要改变孔壁，不能据此宣称整个覆盖平面获得了虹彩。

推荐的接入位置是**顶面的反射分支**：保留清晰直透、孔壁 IOR 1.45 和已有去重影处理；先在独立反射材质试验薄膜，再弱化其色度。不要直接把带薄膜的完整玻璃 BSDF 替换进顶面，否则可能重新改变透射或折射。薄膜原生模型也可能改变透射，所以“仅反射着色”是展示用途的受控近似，不是完整光学仿真。[4]

另一个关键限制：薄膜 IOR 若等于基底 IOR，光学界面消失，原生薄膜效果也会消失。[4] 不能把 film IOR 与 glass IOR 都设成 1.45，就期待出现 oxide-on-silicon 的颜色。真实芯片应参考实际的 oxide/nitride/silicon/metal 堆叠；当前渲染 cladding 体积厚度 7.981 µm 不等于一个已知的纳米薄膜涂层。精确复刻需要膜厚及光学常数，必要时预计算多层光谱反射，再转为反射颜色查找表。

## 灯光方向

建议先用白色、接近观察轴的反射照明，加入很弱的白色补光以保留孔壁形状；保持白平衡和曝光一致，并避免高光过曝。可比较较集中与较宽的照明角度，观察虹彩是否被角度平均而减弱。它是显微镜反射照明的外观近似，不是把普通面光源当作完整显微镜光路。Nikon 给出了同轴、环形和斜入射反射照明的区别。[6]

彩色灯可以制造彩色反光，但并不能验证薄膜或衍射机制，并且容易同时染色金属、背景与孔壁。当前优先级低于白光加材料响应。增加折射色散或图像色差也不是优先方向，因为它可能损害刚恢复的器件轮廓清晰度。

## 最小下一步试验（提议，尚未执行）

先用同一共享材质，在包含平面、开孔和下层细线的通用测试几何上做四张对照：

1. 当前照明 + 干涉关闭。
2. 白色近同轴照明 + 干涉关闭。
3. 当前照明 + 弱干涉反射。
4. 白色近同轴照明 + 弱干涉反射。

先确认一个有光学界面差异的明确膜层模型；膜厚扫描可以从数百 nm 的可视化示例开始，但不能将试验值认定为真实器件参数。独立控制反射的色度强度，保持透射白色，不用降低 IOR 或提高透明度来调淡颜色。

通过条件：能看到轻微随角度改变的反射颜色，细线仍清楚，开孔与覆盖区可辨认，整体没有冷色塑料感。之后再换一个视角，并以 TX checkered 和 Ramzi 回归检查；测试例不得引入按文件名、器件名或坐标硬编码的材质分支。若效果主要需要随机彩色噪声或染色透射才能成立，则否定该路线，重新核对参考显微照片的机制。

当前建议：先验证薄膜反射方案；是否成为可选 microscope 外观、是否推广到三个配色，均未决定。已发布的中性玻璃设置没有继续修改。

## 依据

1. [BYU Cleanroom：SiO₂/SiN 膜厚与观察角度颜色](https://www.cleanroom.byu.edu/color_chart)
2. [MKS / Newport：Diffraction Grating Handbook](https://api.p1.mks.com/mam/celum/celum_assets/np/resources/MKS_Diffraction_Grating_Handbook.pdf?0=)
3. [Nikon MicroscopyU：Reflected Light DIC Microscopy](https://www.microscopyu.com/techniques/dic/reflected-light-dic-microscopy)
4. [Blender Manual：Principled BSDF / Thin Film](https://docs.blender.org/manual/id/5.1/render/shader_nodes/shader/principled.html#thin-film)
5. [Blender 5.0：Metallic Thin Film](https://developer.blender.org/docs/release_notes/5.0/cycles/#metallic-thin-film)
6. [Nikon MicroscopyU：Reflected (Episcopic) Light Illumination](https://www.microscopyu.com/techniques/stereomicroscopy/reflected-episcopic-light-illumination)

本地证据：`scripts/aim_build_blender_scene.py` 的实际材质连接；`disk_array_oblique_300mm.yaml` 的现有 Cycles/Sun/AgX 设置；实际 Blender 节点输入检查。上述来源支持机制与能力判断，不构成当前项目虹彩效果已渲染通过的证明。
