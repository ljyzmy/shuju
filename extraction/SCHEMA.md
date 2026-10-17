# 公司资料抽取 → 可入库字段契约

对齐 qzd_db：`company` / `company_list`，供「企业画像 → 政策匹配 → 材料缺口」链路使用。

## 主键规则
1. 优先 `uscc`（统一社会信用代码）→ 与 `company.source_id` / `company.uscc` 对齐
2. 无 uscc：规范化全称模糊挂 `company_id`（`match_method=name_fuzzy`）
3. 挂不上：`subject_status=pending_verify`（待核验主体），等执照补齐再落库

## 输出对象（JSON）
| 字段 | 说明 |
|---|---|
| `subject` | 主体：`name`, `uscc`, `company_id`, `subject_status`, `match_method` |
| `business_tags` | 业务标签（服务商画像或客户行业标签） |
| `list_items` | 资质/名单项，对齐 `company_list`：`list_type`, `list_name`, `issuer`, `province`, `city`, `year` |
| `evidence` | 证据引用：`kind`（patent/paper/material/soft_copyright/…）, `ref`, `source_file`, `note` |
| `gaps` | 缺口清单：`field`, `why`, `suggested_material` |
| `material_meta` | 材料用途：`internal_training` / `external_intro` / `exam` / `case_review` 等 |
| `source_files` | 源文件清单（推断标题 + 文件 id） |

## list_type 口径（与现有名单一致）
`高新技术企业` | `科技型中小企业` | `创新型中小企业` | `专精特新` | `专精特新小巨人` | `瞪羚` | `独角兽` | `企业技术中心` | `其他`

禁止编造：材料未写明的 uscc、客户全称、证书编号一律进 `gaps`，不得填假值。
