export type LaunchpadTabKey = "lingxi" | "common" | "ai";

export interface LaunchpadTab {
  key: LaunchpadTabKey;
  label: string;
}

export interface LaunchpadEntry {
  id: string;
  tab: LaunchpadTabKey;
  groupId: string;
  groupName: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  systemKey?: string;
  url?: string;
  unavailableReason?: string;
}

export interface LaunchpadGroup {
  id: string;
  tab: LaunchpadTabKey;
  name: string;
  icon?: string;
  entries: LaunchpadEntry[];
}

export type LaunchpadDestination =
  | { kind: "url"; href: string }
  | { kind: "unavailable"; reason: string };

export const launchpadTabs: LaunchpadTab[] = [
  { key: "lingxi", label: "灵犀平台" },
  { key: "common", label: "网页导航" },
  { key: "ai", label: "AI应用" },
];

const DEFAULT_NON_GMP_BASE_URL = "http://10.56.0.25:8189";
const DEFAULT_LEGACY_WEBUI_FRAME_URL =
  "http://10.56.0.25:8189/#/TaskManagement/indexSpace/";
const LEGACY_SYSTEM_ENTRY_PATHS: Record<string, string> = {
  ADEquipment: "/ADEquipment/Management/Management",
  Admin: "/Admin/userManage",
  AdministrativeAssetManagement: "/AdministrativeAssetManagement/admin/assetManage",
  ADQC: "/ADQC/Material/Warehouse",
  ADSampleSender: "/ADSampleSender/overview",
  Contract: "/Contract/contractList",
  DataAcquisition: "/BioReactor/Management/Management",
  DataAcquisitionManage: "/DataAcquisitionManage/deviceView",
  DataAcquisitionRD: "/BioReactor/RD/Management/Management",
  DSPCalculate: "/DSPCalculate",
  DSPD: "/DSPD/RecordBatch/myTask",
  FXProductWarehouseManagement: "/FXProductWarehouseManagement",
  ITDeviceManagement: "/ITDeviceManage/overView",
  MFGDataManagement: "/MFGDataManagement/usp/deviceView",
  Molecule: "/Molecule/overView/overView",
  PDD: "/DSPD/PDD/project",
  PDSampleSender: "/PDSampleSender/overview",
  Product: "/product/home",
  ProjectInfo: "/ProjectInfo/Project/Project",
  ProjectManagement: "/ProjectManagement/V2.0/Visualize/OverView",
  QAArchiveMS: "/QAArchiveMS/archiveLedger",
  Quotation: "/Quotation/customerList",
  RAGFlowSOP: "/AI/RAGFlowSOP",
  RDDeviceManage: "/RdDevice/overview",
  RDELN: "/RDELN/MyExperiment/OnDesign",
  RDMaterialManage: "/RD/RDmaterialManage/overView/overView",
  RQAFileManagement: "/RQA/recordMaintain",
  SampleSender: "/RDSampleSender/dashboard/overview",
  ToxiResidueCal: "/MFG/ToxicResidueCal/RecordsManagement",
  VMS: "/VMS/home",
  WordReview: "/AI/WordReview",
};

function getLegacyNonGmpBaseUrl(): string {
  const viteEnv = (
    import.meta as unknown as { env?: Record<string, string | undefined> }
  ).env;
  return (viteEnv?.VITE_LEGACY_NONGMP_URL || DEFAULT_NON_GMP_BASE_URL).replace(
    /\/$/,
    "",
  );
}

export function getLegacyWebUiFrameUrl(): string {
  const viteEnv = (
    import.meta as unknown as { env?: Record<string, string | undefined> }
  ).env;
  return (
    viteEnv?.VITE_LEGACY_WEBUI_FRAME_URL || DEFAULT_LEGACY_WEBUI_FRAME_URL
  );
}

export function buildLegacySystemUrl(
  systemKey: string,
  baseUrl = getLegacyNonGmpBaseUrl(),
): string | null {
  const entryPath = LEGACY_SYSTEM_ENTRY_PATHS[systemKey];
  if (!entryPath) {
    return null;
  }
  return baseUrl + "/#" + entryPath;
}

export const launchpadGroups: LaunchpadGroup[] = [
  {
    "id": "lingxi-RD",
    "tab": "lingxi",
    "name": "研发",
    "icon": "el-icon-cpu",
    "entries": [
      {
        "id": "lingxi-RD-0-IT资产管理",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "IT资产管理",
        "icon": "el-icon-monitor",
        "color": "#409EFF",
        "systemKey": "ITDeviceManagement"
      },
      {
        "id": "lingxi-RD-1-分析检测",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "分析检测",
        "icon": "sampler",
        "color": "#67C23A",
        "systemKey": "SampleSender"
      },
      {
        "id": "lingxi-RD-2-分子生物",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "分子生物",
        "icon": "el-icon-attract",
        "color": "#E6A23C",
        "systemKey": "Molecule"
      },
      {
        "id": "lingxi-RD-3-设备管理",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "设备管理",
        "icon": "el-icon-setting",
        "color": "#F56C6C",
        "systemKey": "RDDeviceManage"
      },
      {
        "id": "lingxi-RD-4-物料管理",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "物料管理",
        "icon": "el-icon-box",
        "color": "#909399",
        "systemKey": "RDMaterialManage"
      },
      {
        "id": "lingxi-RD-5-反应器数采",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "反应器数采",
        "icon": "el-icon-odometer",
        "color": "#409EFF",
        "systemKey": "DataAcquisitionRD"
      },
      {
        "id": "lingxi-RD-6-数据采集平台",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "数据采集平台",
        "icon": "el-icon-data-board",
        "color": "#67C23A",
        "systemKey": "DataAcquisitionManage"
      },
      {
        "id": "lingxi-RD-7-RD-ELN",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "RD-ELN",
        "icon": "el-icon-notebook-1",
        "color": "#67C23A",
        "systemKey": "RDELN"
      },
      {
        "id": "lingxi-RD-8-管理后台",
        "tab": "lingxi",
        "groupId": "lingxi-RD",
        "groupName": "研发",
        "name": "管理后台",
        "icon": "el-icon-data-board",
        "color": "#67C23A",
        "systemKey": "Admin"
      }
    ]
  },
  {
    "id": "lingxi-PD",
    "tab": "lingxi",
    "name": "工艺开发",
    "icon": "el-icon-setting",
    "entries": [
      {
        "id": "lingxi-PD-0-反应器数采",
        "tab": "lingxi",
        "groupId": "lingxi-PD",
        "groupName": "工艺开发",
        "name": "反应器数采",
        "icon": "el-icon-odometer",
        "color": "#E6A23C",
        "systemKey": "DataAcquisition"
      },
      {
        "id": "lingxi-PD-1-下游工艺开发",
        "tab": "lingxi",
        "groupId": "lingxi-PD",
        "groupName": "工艺开发",
        "name": "下游工艺开发",
        "icon": "el-icon-guide",
        "color": "#E6A23C",
        "systemKey": "DSPD"
      },
      {
        "id": "lingxi-PD-2-PDD自动计算",
        "tab": "lingxi",
        "groupId": "lingxi-PD",
        "groupName": "工艺开发",
        "name": "PDD自动计算",
        "icon": "el-icon-cpu",
        "color": "#F56C6C",
        "systemKey": "PDD"
      },
      {
        "id": "lingxi-PD-3-小样纯化",
        "tab": "lingxi",
        "groupId": "lingxi-PD",
        "groupName": "工艺开发",
        "name": "小样纯化",
        "icon": "el-icon-document-copy",
        "color": "#409EFF",
        "systemKey": "PDSampleSender"
      }
    ]
  },
  {
    "id": "lingxi-AD",
    "tab": "lingxi",
    "name": "分析检测",
    "icon": "el-icon-zoom-in",
    "entries": [
      {
        "id": "lingxi-AD-0-AD物料仓库",
        "tab": "lingxi",
        "groupId": "lingxi-AD",
        "groupName": "分析检测",
        "name": "AD物料仓库",
        "icon": "el-icon-house",
        "color": "#409EFF",
        "systemKey": "ADQC"
      },
      {
        "id": "lingxi-AD-1-AD制剂设备预约",
        "tab": "lingxi",
        "groupId": "lingxi-AD",
        "groupName": "分析检测",
        "name": "AD制剂设备预约",
        "icon": "el-icon-alarm-clock",
        "color": "#E6A23C",
        "systemKey": "ADEquipment"
      },
      {
        "id": "lingxi-AD-2-AD送样系统",
        "tab": "lingxi",
        "groupId": "lingxi-AD",
        "groupName": "分析检测",
        "name": "AD送样系统",
        "icon": "el-icon-position",
        "color": "#67C23A",
        "systemKey": "ADSampleSender"
      }
    ]
  },
  {
    "id": "lingxi-MFG",
    "tab": "lingxi",
    "name": "生产",
    "icon": "el-icon-s-platform",
    "entries": [
      {
        "id": "lingxi-MFG-0-生产批记录",
        "tab": "lingxi",
        "groupId": "lingxi-MFG",
        "groupName": "生产",
        "name": "生产批记录",
        "icon": "el-icon-document-copy",
        "color": "#409EFF",
        "systemKey": "Product"
      },
      {
        "id": "lingxi-MFG-1-生产工艺计算",
        "tab": "lingxi",
        "groupId": "lingxi-MFG",
        "groupName": "生产",
        "name": "生产工艺计算",
        "icon": "el-icon-thumb",
        "color": "#F56C6C",
        "systemKey": "DSPCalculate"
      },
      {
        "id": "lingxi-MFG-2-毒理计算",
        "tab": "lingxi",
        "groupId": "lingxi-MFG",
        "groupName": "生产",
        "name": "毒理计算",
        "icon": "skull",
        "color": "#909399",
        "systemKey": "ToxiResidueCal"
      },
      {
        "id": "lingxi-MFG-3-FX产成品仓库",
        "tab": "lingxi",
        "groupId": "lingxi-MFG",
        "groupName": "生产",
        "name": "FX产成品仓库",
        "icon": "el-icon-truck",
        "color": "#67C23A",
        "systemKey": "FXProductWarehouseManagement"
      },
      {
        "id": "lingxi-MFG-4-中试数据管理",
        "tab": "lingxi",
        "groupId": "lingxi-MFG",
        "groupName": "生产",
        "name": "中试数据管理",
        "icon": "el-icon-document-copy",
        "color": "#F56C6C",
        "systemKey": "MFGDataManagement"
      }
    ]
  },
  {
    "id": "lingxi-PM",
    "tab": "lingxi",
    "name": "项目管理",
    "icon": "el-icon-s-flag",
    "entries": [
      {
        "id": "lingxi-PM-0-项目管理系统",
        "tab": "lingxi",
        "groupId": "lingxi-PM",
        "groupName": "项目管理",
        "name": "项目管理系统",
        "icon": "el-icon-folder-opened",
        "color": "#409EFF",
        "systemKey": "ProjectManagement"
      },
      {
        "id": "lingxi-PM-1-项目信息系统",
        "tab": "lingxi",
        "groupId": "lingxi-PM",
        "groupName": "项目管理",
        "name": "项目信息系统",
        "icon": "el-icon-info",
        "color": "#E6A23C",
        "systemKey": "ProjectInfo"
      }
    ]
  },
  {
    "id": "lingxi-QA",
    "tab": "lingxi",
    "name": "质量",
    "icon": "el-icon-s-check",
    "entries": [
      {
        "id": "lingxi-QA-0-研发记录管理",
        "tab": "lingxi",
        "groupId": "lingxi-QA",
        "groupName": "质量",
        "name": "研发记录管理",
        "icon": "el-icon-notebook-2",
        "color": "#409EFF",
        "systemKey": "RQAFileManagement"
      },
      {
        "id": "lingxi-QA-1-QA档案管理",
        "tab": "lingxi",
        "groupId": "lingxi-QA",
        "groupName": "质量",
        "name": "QA档案管理",
        "icon": "el-icon-folder-opened",
        "color": "#67C23A",
        "systemKey": "QAArchiveMS"
      }
    ]
  },
  {
    "id": "lingxi-BD",
    "tab": "lingxi",
    "name": "商务",
    "icon": "el-icon-s-custom",
    "entries": [
      {
        "id": "lingxi-BD-0-报价系统",
        "tab": "lingxi",
        "groupId": "lingxi-BD",
        "groupName": "商务",
        "name": "报价系统",
        "icon": "el-icon-bank-card",
        "color": "#E6A23C",
        "systemKey": "Quotation"
      },
      {
        "id": "lingxi-BD-1-合同管理系统",
        "tab": "lingxi",
        "groupId": "lingxi-BD",
        "groupName": "商务",
        "name": "合同管理系统",
        "icon": "el-icon-s-order",
        "color": "#409EFF",
        "systemKey": "Contract"
      }
    ]
  },
  {
    "id": "lingxi-Admin",
    "tab": "lingxi",
    "name": "行政管理",
    "icon": "el-icon-office-building",
    "entries": [
      {
        "id": "lingxi-Admin-0-VMS访客管理",
        "tab": "lingxi",
        "groupId": "lingxi-Admin",
        "groupName": "行政管理",
        "name": "VMS访客管理",
        "icon": "el-icon-user",
        "color": "#409EFF",
        "systemKey": "VMS"
      },
      {
        "id": "lingxi-Admin-1-行政列管品管理",
        "tab": "lingxi",
        "groupId": "lingxi-Admin",
        "groupName": "行政管理",
        "name": "行政列管品管理",
        "icon": "el-icon-lock",
        "color": "#F56C6C",
        "systemKey": "AdministrativeAssetManagement"
      }
    ]
  },
  {
    "id": "common-cat-0",
    "tab": "common",
    "name": "内网登录",
    "icon": "el-icon-office-building",
    "entries": [
      {
        "id": "common-cat-0-0-公司规章制度",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "公司规章制度",
        "description": "公司规章制度网站",
        "icon": "website_icon.png",
        "url": "http://kb.intbio.com:5137/"
      },
      {
        "id": "common-cat-0-1-QMS质量管理系统",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "QMS质量管理系统",
        "description": "DMS&TMS系统",
        "icon": "QMS_icon.png",
        "url": "http://dms.intbio.com/portal/index.html#/login"
      },
      {
        "id": "common-cat-0-2-vDrive(内部)",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "vDrive(内部)",
        "description": "DataRoom_内部员工数据分享、共同编辑以及传输平台",
        "icon": "内部网盘.png",
        "url": "http://vdr-internal.intbio.com/"
      },
      {
        "id": "common-cat-0-3-OA",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "OA",
        "description": "OA流程管理系统",
        "icon": "OA_icon.png",
        "url": "http://oa.intbio.com:8090/login.jsp"
      },
      {
        "id": "common-cat-0-4-ERP",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "ERP",
        "description": "请通过用友客户端登录",
        "icon": "erp_icon.png",
        "url": "http://erp.intbio.com:8080/"
      },
      {
        "id": "common-cat-0-5-小智",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "小智",
        "description": "小智AI - 要么随波逐流，要么驾驭小智AI，即刻登录，掌控全局。",
        "icon": "992a2dfcd3b932d05562580ad636b4c5.jpeg",
        "url": "http://innovateX-AI.intbio.com:2026/workspace"
      },
      {
        "id": "common-cat-0-6-公司邮箱",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "公司邮箱",
        "description": "企业邮箱",
        "icon": "emial.png",
        "url": "https://qiye.aliyun.com/"
      },
      {
        "id": "common-cat-0-7-GMP域账号自助管理",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "GMP域账号自助管理",
        "description": "GMP域账号密码重置",
        "icon": "pwd.jpg",
        "url": "http://pwd.intbio.com/RDWeb/Pages/zh-CN/password.aspx"
      },
      {
        "id": "common-cat-0-8-合理化建议",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "合理化建议",
        "description": "公司合理化建议",
        "icon": "d9576b3de14945df873fb75acd8f8342.jpg",
        "url": "http://vdr-internal.intbio.com/index.html#doc/enterprise/2121"
      },
      {
        "id": "common-cat-0-9-员工满意度调研",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "员工满意度调研",
        "description": "员工满意度调研",
        "icon": "满意度调研.jpg",
        "url": "https://www.wjx.cn/vm/YO9HJ5f.aspx#"
      },
      {
        "id": "common-cat-0-10-vDrive(外部)",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "vDrive(外部)",
        "description": "DataRoom_PM、投融资、BD、QA等对外数据分享、共同编辑以及传输平台",
        "icon": "我的网盘.png",
        "url": "http://vdr.intellectivebio.com:6777/"
      },
      {
        "id": "common-cat-0-11-公司官网",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "公司官网",
        "description": "企业官方网站",
        "icon": "innovateX.png",
        "url": "https://www.intellectivebio.com"
      },
      {
        "id": "common-cat-0-12-VR",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "VR",
        "description": "常熟总部VR",
        "icon": "vr_icon.png",
        "url": "http://vr-internal.intbio.com:8090/"
      },
      {
        "id": "common-cat-0-13-WMS系统",
        "tab": "common",
        "groupId": "common-cat-0",
        "groupName": "内网登录",
        "name": "WMS系统",
        "description": "仓库管理系统",
        "icon": "WMS.png",
        "url": "http://wms.intbio.com/#/login"
      }
    ]
  },
  {
    "id": "common-cat-1",
    "tab": "common",
    "name": "AI",
    "icon": "el-icon-cpu",
    "entries": [
      {
        "id": "common-cat-1-0-Gemini",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "Gemini",
        "description": "最新Gemini3",
        "icon": "gemini-color.png",
        "url": "https://gemini.google.com/app"
      },
      {
        "id": "common-cat-1-1-DeepSeek",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "DeepSeek",
        "description": "深度求索，探索未至之境",
        "icon": "deepseek.jpg",
        "url": "https://chat.deepseek.com/sign_in"
      },
      {
        "id": "common-cat-1-2-Kimi",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "Kimi",
        "description": "月之暗面",
        "icon": "kimi_icon.png",
        "url": "https://kimi.moonshot.cn/chat/cogtsnkudu60vho4b50g"
      },
      {
        "id": "common-cat-1-3-豆包",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "豆包",
        "description": "字节跳动",
        "icon": "fill_w720_h480_g0_mark_1708362360-doubao_logo_new.jpg",
        "url": "https://www.doubao.com/chat/"
      },
      {
        "id": "common-cat-1-4-智谱清言",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "智谱清言",
        "description": "智谱AI",
        "icon": "qinghua.jpg",
        "url": "https://chatglm.cn/main/alltoolsdetail"
      },
      {
        "id": "common-cat-1-5-通义千问",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "通义千问",
        "description": "阿里大模型",
        "icon": "tongyi_icon.png",
        "url": "https://tongyi.aliyun.com/qianwen/"
      },
      {
        "id": "common-cat-1-6-文心一言",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "文心一言",
        "description": "百度大模型",
        "icon": "yiyan_icon.png",
        "url": "https://yiyan.baidu.com/"
      },
      {
        "id": "common-cat-1-7-POE",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "POE",
        "description": "注册免费用各种顶级大模型",
        "icon": "poe_icon.png",
        "url": "https://poe.com/login"
      },
      {
        "id": "common-cat-1-8-秘塔AI",
        "tab": "common",
        "groupId": "common-cat-1",
        "groupName": "AI",
        "name": "秘塔AI",
        "description": "写论文，了解专业知识的神器",
        "icon": "mita_icon.png",
        "url": "https://metaso.cn/"
      }
    ]
  },
  {
    "id": "common-cat-2",
    "tab": "common",
    "name": "翻译",
    "icon": "el-icon-connection",
    "entries": [
      {
        "id": "common-cat-2-0-沉浸式翻译",
        "tab": "common",
        "groupId": "common-cat-2",
        "groupName": "翻译",
        "name": "沉浸式翻译",
        "description": "保持排版",
        "icon": "下载.png",
        "url": "https://app.immersivetranslate.com/"
      },
      {
        "id": "common-cat-2-1-deepL",
        "tab": "common",
        "groupId": "common-cat-2",
        "groupName": "翻译",
        "name": "deepL",
        "description": "号称最准确的翻译软件",
        "icon": "deepL_icon.png",
        "url": "https://www.deepl.com/translator"
      },
      {
        "id": "common-cat-2-2-谷歌翻译",
        "tab": "common",
        "groupId": "common-cat-2",
        "groupName": "翻译",
        "name": "谷歌翻译",
        "description": "老牌翻译",
        "icon": "google_icon.png",
        "url": "https://translate.google.com/"
      }
    ]
  },
  {
    "id": "common-cat-3",
    "tab": "common",
    "name": "绘图",
    "icon": "el-icon-picture-outline",
    "entries": [
      {
        "id": "common-cat-3-0-Excalidraw",
        "tab": "common",
        "groupId": "common-cat-3",
        "groupName": "绘图",
        "name": "Excalidraw",
        "description": "强大免费，可协作的在线手绘风格工具",
        "icon": "exca_icon.png",
        "url": "https://excalidraw.com/"
      },
      {
        "id": "common-cat-3-1-Biorender",
        "tab": "common",
        "groupId": "common-cat-3",
        "groupName": "绘图",
        "name": "Biorender",
        "description": "强大，卡通风格研绘图工具",
        "icon": "bioRENDER_icon.png",
        "url": "https://app.biorender.com/user/signup"
      },
      {
        "id": "common-cat-3-2-Figdraw",
        "tab": "common",
        "groupId": "common-cat-3",
        "groupName": "绘图",
        "name": "Figdraw",
        "description": "国产biorender替代",
        "icon": "FiG_icon.png",
        "url": "https://www.figdraw.com/static/index.html#/"
      }
    ]
  },
  {
    "id": "common-cat-4",
    "tab": "common",
    "name": "文献检索",
    "icon": "el-icon-search",
    "entries": [
      {
        "id": "common-cat-4-0-谷歌学术",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "谷歌学术",
        "description": "科研人必备，简单好用",
        "icon": "scholar_icon.png",
        "url": "https://scholar.google.com/"
      },
      {
        "id": "common-cat-4-1-Pubmed",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "Pubmed",
        "description": "大杂烩",
        "icon": "NIH_icon.png",
        "url": "https://pubmed.ncbi.nlm.nih.gov/"
      },
      {
        "id": "common-cat-4-2-易文献",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "易文献",
        "description": "官方机构，注册后免费下载文献",
        "icon": "sgst_icon.png",
        "url": "http://lib.sgst.cn/"
      },
      {
        "id": "common-cat-4-3-SCI-hub",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "SCI-hub",
        "description": "强大的文献下载网站，网址经常变",
        "icon": "Sci-hub_icon.png",
        "url": "https://www.sci-hub.st/"
      },
      {
        "id": "common-cat-4-4-Library-Genesis",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "Library Genesis",
        "description": "强大的图书下载网站，网址经常变",
        "icon": "libgene_icon.png",
        "url": "https://libgen.re/scimag/"
      },
      {
        "id": "common-cat-4-5-bookfi",
        "tab": "common",
        "groupId": "common-cat-4",
        "groupName": "文献检索",
        "name": "bookfi",
        "description": "强大的图书下载网站，网址经常变",
        "icon": "zlib_icon.png",
        "url": "https://z-library.cc/"
      }
    ]
  },
  {
    "id": "common-cat-5",
    "tab": "common",
    "name": "文献期刊",
    "icon": "el-icon-reading",
    "entries": [
      {
        "id": "common-cat-5-0-Mabs",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Mabs",
        "description": "抗体人必读的顶级期刊",
        "icon": "a.jpg",
        "url": "http://www.tandfonline.com/action/showAxaArticles?journalCode=kmab20"
      },
      {
        "id": "common-cat-5-1-Antibodies",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Antibodies",
        "description": "质量一般，胜在免费",
        "icon": "MDPI_icon.png",
        "url": "https://www.mdpi.com/journal/antibodies"
      },
      {
        "id": "common-cat-5-2-Biotechnology-and-Bioengineering",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Biotechnology and Bioengineering",
        "description": "工艺人必读的顶级期刊",
        "icon": "wiley_icon.png",
        "url": "http://onlinelibrary.wiley.com/journal/10.1002/(ISSN)1097-0290"
      },
      {
        "id": "common-cat-5-3-Biotechnology-Progress",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Biotechnology Progress",
        "description": "工艺人必读的期刊",
        "icon": "AIChE_icon.png",
        "url": "https://aiche.onlinelibrary.wiley.com/journal/15206033?journalRedirectCheck=true"
      },
      {
        "id": "common-cat-5-4-Journal-of-Chromatography-A",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Journal of Chromatography A",
        "description": "分析人必读期刊",
        "icon": "ScienceD_icon.png",
        "url": "https://www.sciencedirect.com/journal/journal-of-chromatography-a"
      },
      {
        "id": "common-cat-5-5-Journal-of-Chromatography-B",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "Journal of Chromatography B",
        "description": "分析人必读期刊",
        "icon": "ScienceD_icon.png",
        "url": "https://www.sciencedirect.com/journal/journal-of-chromatography-b"
      },
      {
        "id": "common-cat-5-6-European-Journal-of-Pharmaceutics-and-Biopharmaceutics",
        "tab": "common",
        "groupId": "common-cat-5",
        "groupName": "文献期刊",
        "name": "European Journal of Pharmaceutics and Biopharmaceutics",
        "description": "制剂人必读期刊",
        "icon": "ScienceD_icon.png",
        "url": "https://www.sciencedirect.com/journal/european-journal-of-pharmaceutics-and-biopharmaceutics"
      }
    ]
  },
  {
    "id": "common-cat-6",
    "tab": "common",
    "name": "专利检索",
    "icon": "el-icon-search",
    "entries": [
      {
        "id": "common-cat-6-0-The-Lens",
        "tab": "common",
        "groupId": "common-cat-6",
        "groupName": "专利检索",
        "name": "The Lens",
        "description": "强大，免费，专利搜索和下载",
        "icon": "lens_icon.png",
        "url": "https://www.lens.org/"
      },
      {
        "id": "common-cat-6-1-USPTO",
        "tab": "common",
        "groupId": "common-cat-6",
        "groupName": "专利检索",
        "name": "USPTO",
        "description": "美国专利搜索下载",
        "icon": "uspto_icon.png",
        "url": "https://patentcenter.uspto.gov/search?query="
      },
      {
        "id": "common-cat-6-2-Free-patent-online",
        "tab": "common",
        "groupId": "common-cat-6",
        "groupName": "专利检索",
        "name": "Free patent online",
        "description": "免费专利搜索下载",
        "icon": "fpo_icon.png",
        "url": "https://www.freepatentsonline.com/"
      },
      {
        "id": "common-cat-6-3-专利之星",
        "tab": "common",
        "groupId": "common-cat-6",
        "groupName": "专利检索",
        "name": "专利之星",
        "description": "中国专利，注册后免费下载",
        "icon": "cprs_icon.png",
        "url": "https://cprs.patentstar.com.cn/Search/Index"
      },
      {
        "id": "common-cat-6-4-谷歌专利",
        "tab": "common",
        "groupId": "common-cat-6",
        "groupName": "专利检索",
        "name": "谷歌专利",
        "description": "强大，免费，专利搜索和下载",
        "icon": "patents_icon.png",
        "url": "https://patents.google.com/"
      }
    ]
  },
  {
    "id": "common-cat-7",
    "tab": "common",
    "name": "药物蛋白数据库",
    "icon": "el-icon-coin",
    "entries": [
      {
        "id": "common-cat-7-0-FDA-Approved-Drug-Products",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "FDA Approved Drug Products",
        "description": "FDA批准药物",
        "icon": "FDA_icon.png",
        "url": "https://www.accessdata.fda.gov/scripts/cder/daf/"
      },
      {
        "id": "common-cat-7-1-PMDA-Approved-Drug-Products",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "PMDA Approved Drug Products",
        "description": "PMDA批准药物",
        "icon": "pmda_icon.png",
        "url": "https://www.pmda.go.jp/english/review-services/reviews/approved-information/drugs/0003.html"
      },
      {
        "id": "common-cat-7-2-EMA-Approved-Drug-Products",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "EMA Approved Drug Products",
        "description": "EMA批准药物",
        "icon": "EUMed_icon.png",
        "url": "https://www.ema.europa.eu/en/medicines"
      },
      {
        "id": "common-cat-7-3-Drugbank",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "Drugbank",
        "description": "药物信息查询大全",
        "icon": "drugbank_icon.png",
        "url": "https://www.drugbank.com/"
      },
      {
        "id": "common-cat-7-4-The-Structural-Antibody-Database",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "The Structural Antibody Database",
        "description": "抗体序列数据库",
        "icon": "SAbDab_icon.png",
        "url": "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab"
      },
      {
        "id": "common-cat-7-5-IMGT",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "IMGT",
        "description": "强大的免疫组库",
        "icon": "IMGT_icon.png",
        "url": "https://www.imgt.org/"
      },
      {
        "id": "common-cat-7-6-RCBS",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "RCBS",
        "description": "蛋白结构数据库",
        "icon": "PDB_icon.png",
        "url": "https://www.rcsb.org/"
      },
      {
        "id": "common-cat-7-7-Uniprot",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "Uniprot",
        "description": "蛋白信息、结构大全",
        "icon": "uniprot_icon.png",
        "url": "https://www.uniprot.org/"
      },
      {
        "id": "common-cat-7-8-NCBI",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "NCBI",
        "description": "老牌经典",
        "icon": "NIH_icon.png",
        "url": "https://www.ncbi.nlm.nih.gov/gene/"
      },
      {
        "id": "common-cat-7-9-insight数据库",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "insight数据库",
        "description": "公司有买账号",
        "icon": "insight_icon.png",
        "url": "https://db.dxy.cn/v5/home"
      },
      {
        "id": "common-cat-7-10-中国药品价格查询",
        "tab": "common",
        "groupId": "common-cat-7",
        "groupName": "药物蛋白数据库",
        "name": "中国药品价格查询",
        "description": "医保价格",
        "icon": "pkufh_icon.png",
        "url": "https://www.pkufh.com/Interactions/HisInquiry/MedicalPrice?medicalPriceTypeId=1"
      }
    ]
  },
  {
    "id": "common-cat-8",
    "tab": "common",
    "name": "预测工具",
    "icon": "el-icon-s-data",
    "entries": [
      {
        "id": "common-cat-8-0-SMS",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "SMS",
        "description": "序列处理工具包",
        "icon": "sms_icon.png",
        "url": "http://www.bio-soft.net/sms/"
      },
      {
        "id": "common-cat-8-1-SignalP-–-6.0",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "SignalP – 6.0",
        "description": "信号肽预测",
        "icon": "dtu_icon-1.png",
        "url": "https://services.healthtech.dtu.dk/services/SignalP-6.0/"
      },
      {
        "id": "common-cat-8-2-ProtParam",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "ProtParam",
        "description": "消光系数和等电点预测",
        "icon": "sib_icon.png",
        "url": "https://web.expasy.org/protparam/"
      },
      {
        "id": "common-cat-8-3-Isoelectric-Point-Calculator-2.0",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "Isoelectric Point Calculator 2.0",
        "description": "等电点预测",
        "icon": "ipc_icon.png",
        "url": "https://ipc2.mimuw.edu.pl/"
      },
      {
        "id": "common-cat-8-4-ExPAsy",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "ExPAsy",
        "description": "各种预测工具大全",
        "icon": "sib_icon.png",
        "url": "https://www.expasy.org/"
      },
      {
        "id": "common-cat-8-5-Unimod",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "Unimod",
        "description": "质谱修饰分子量",
        "icon": "unimod_icon.png",
        "url": "https://www.unimod.org/login.php?message=expired"
      },
      {
        "id": "common-cat-8-6-YinOyang",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "YinOyang",
        "description": "O糖修饰预测",
        "icon": "dtu_icon-1.png",
        "url": "https://services.healthtech.dtu.dk/services/YinOYang-1.2/"
      },
      {
        "id": "common-cat-8-7-abysis",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "abysis",
        "description": "CDR预测和人源化",
        "icon": "abY_icon.png",
        "url": "http://www.abysis.org/abysis/"
      },
      {
        "id": "common-cat-8-8-TAP",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "TAP",
        "description": "成药性预测",
        "icon": "SAbDab_icon.png",
        "url": "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabpred/tap"
      },
      {
        "id": "common-cat-8-9-protein-sol",
        "tab": "common",
        "groupId": "common-cat-8",
        "groupName": "预测工具",
        "name": "protein-sol",
        "description": "成药性预测",
        "icon": "protein_icon.png",
        "url": "https://protein-sol.manchester.ac.uk/"
      }
    ]
  },
  {
    "id": "common-cat-9",
    "tab": "common",
    "name": "中国药监机构或协会",
    "icon": "el-icon-s-check",
    "entries": [
      {
        "id": "common-cat-9-0-国家市场监督管理总局",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "国家市场监督管理总局",
        "description": "国务院直属机构，管理NMPA",
        "icon": "yaojian_icon.png",
        "url": "https://www.samr.gov.cn/jg/index.html"
      },
      {
        "id": "common-cat-9-1-国家药品监督管理局",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "国家药品监督管理局",
        "description": "NMPA主页，含法规文件、药监动态等",
        "icon": "yaojian_icon.png",
        "url": "https://www.nmpa.gov.cn/index.html"
      },
      {
        "id": "common-cat-9-2-药品数据查询（NMPA）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "药品数据查询（NMPA）",
        "icon": "yaojian_icon.png",
        "url": "https://www.nmpa.gov.cn/datasearch/home-index.html#category=yp"
      },
      {
        "id": "common-cat-9-3-药品审评中心",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "药品审评中心",
        "description": "CDE主页，含政策法规、信息公开、主题专栏等",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/"
      },
      {
        "id": "common-cat-9-4-受理品种查询（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "受理品种查询（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d"
      },
      {
        "id": "common-cat-9-5-共性问题回答（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "共性问题回答（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/07edef25f1e7354bfd8490baa0ce056b"
      },
      {
        "id": "common-cat-9-6-审评任务公示（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "审评任务公示（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/369ac7cfeb67c6000c33f85e6f374044"
      },
      {
        "id": "common-cat-9-7-临床默示许可查询（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "临床默示许可查询（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c"
      },
      {
        "id": "common-cat-9-8-上市药品信息（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "上市药品信息（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/b40868b5e21c038a6aa8b4319d21b07d"
      },
      {
        "id": "common-cat-9-9-原辅包登记信息（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "原辅包登记信息（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/main/xxgk/listpage/ba7aed094c29ae31467c0a35463a716e"
      },
      {
        "id": "common-cat-9-10-指导原则专栏（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "指导原则专栏（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/zdyz/index"
      },
      {
        "id": "common-cat-9-11-ICH工作办公室专栏（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "ICH工作办公室专栏（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/ichWeb/index.jsp"
      },
      {
        "id": "common-cat-9-12-eCTD专栏（CDE）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "eCTD专栏（CDE）",
        "icon": "NMPA_icon.png",
        "url": "https://www.cde.org.cn/ectd/index"
      },
      {
        "id": "common-cat-9-13-中国食品药品检定研究院",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "中国食品药品检定研究院",
        "description": "NIFDC主页，负责药品注册检验等",
        "icon": "nifdc_icon.png",
        "url": "https://www.nifdc.org.cn/nifdc/"
      },
      {
        "id": "common-cat-9-14-国家药典委员会",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "国家药典委员会",
        "description": "CPC主页，负责药典编制、国家药品标准和药品通用名核准等",
        "icon": "ChP_icon.png",
        "url": "https://www.chp.org.cn/#/index"
      },
      {
        "id": "common-cat-9-15-食品药品审核查验中心",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "食品药品审核查验中心",
        "description": "CFDI主页，负责药品现场检查等",
        "icon": "cfdi_icon.png",
        "url": "https://www.cfdi.org.cn/cfdi"
      },
      {
        "id": "common-cat-9-16-药品审评检查长三角分中心",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "药品审评检查长三角分中心",
        "description": "CDE和CFDI在长三角的分中心",
        "icon": "ydcdei_icon.png",
        "url": "https://www.ydcdei.org.cn/"
      },
      {
        "id": "common-cat-9-17-江苏省药品监督管理局",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "江苏省药品监督管理局",
        "description": "JSMPA主页",
        "icon": "yaojian_icon.png",
        "url": "http://da.jiangsu.gov.cn/"
      },
      {
        "id": "common-cat-9-18-药小问普法园地专栏（JSMPA）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "\"药小问\"普法园地专栏（JSMPA）",
        "icon": "cnppa_icon.png",
        "url": "https://da.jiangsu.gov.cn/col/col84698/index.html"
      },
      {
        "id": "common-cat-9-19-已上市药品变更备案专栏（JSMPA）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "已上市药品变更备案专栏（JSMPA）",
        "icon": "yaojian_icon.png",
        "url": "https://da.jiangsu.gov.cn/col/col84704/index.html"
      },
      {
        "id": "common-cat-9-20-咨询信件列表（JSMPA）",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "咨询信件列表（JSMPA）",
        "icon": "yaojian_icon.png",
        "url": "https://da.jiangsu.gov.cn/jact/front/mailpublist.do?sysid=107"
      },
      {
        "id": "common-cat-9-21-中国医药包装协会",
        "tab": "common",
        "groupId": "common-cat-9",
        "groupName": "中国药监机构或协会",
        "name": "中国医药包装协会",
        "description": "CNPPA主页，非营利性社会组织，医药包装领域行业标准等资源",
        "icon": "cnppa_icon.png",
        "url": "https://www.cnppa.org/"
      }
    ]
  },
  {
    "id": "common-cat-10",
    "tab": "common",
    "name": "国外药监机构或协会",
    "icon": "el-icon-s-check",
    "entries": [
      {
        "id": "common-cat-10-0-美国食品药品监督管理局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "美国食品药品监督管理局",
        "description": "FDA主页",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/"
      },
      {
        "id": "common-cat-10-1-指导原则查询（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "指导原则查询（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
      },
      {
        "id": "common-cat-10-2-生物制品指导原则等（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "生物制品指导原则等（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/vaccines-blood-biologics/guidance-compliance-regulatory-information-biologics"
      },
      {
        "id": "common-cat-10-3-IND、BLA申报流程（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "IND、BLA申报流程（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/vaccines-blood-biologics/development-approval-process-cber"
      },
      {
        "id": "common-cat-10-4-FDA科学家对生物制品的科学研究（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "FDA科学家对生物制品的科学研究（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/vaccines-blood-biologics/science-research-biologics"
      },
      {
        "id": "common-cat-10-5-已获批药品信息库（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "已获批药品信息库（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm"
      },
      {
        "id": "common-cat-10-6-紫皮书：FDA批准的生物制品数据库（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "紫皮书：FDA批准的生物制品数据库（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://purplebooksearch.fda.gov/downloads"
      },
      {
        "id": "common-cat-10-7-已获批生物类似药信息库（FDA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "已获批生物类似药信息库（FDA）",
        "icon": "FDA_icon.png",
        "url": "https://www.fda.gov/drugs/biosimilars/biosimilar-product-information"
      },
      {
        "id": "common-cat-10-8-美国联邦法规",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "美国联邦法规",
        "description": "CFR主页，FDA由美国国会及联邦政府授权",
        "icon": "ecfr_icon.png",
        "url": "https://www.ecfr.gov/"
      },
      {
        "id": "common-cat-10-9-欧洲药品管理局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "欧洲药品管理局",
        "description": "EMA主页",
        "icon": "ema_icon.png",
        "url": "https://www.ema.europa.eu/en/homepage"
      },
      {
        "id": "common-cat-10-10-药品查询（EMA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "药品查询（EMA）",
        "icon": "ema_icon.png",
        "url": "https://www.ema.europa.eu/en/medicines"
      },
      {
        "id": "common-cat-10-11-法规指南查询（EMA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "法规指南查询（EMA）",
        "icon": "ema_icon.png",
        "url": "https://www.ema.europa.eu/en/human-regulatory-overview"
      },
      {
        "id": "common-cat-10-12-科学委员会和工作小组（EMA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "科学委员会和工作小组（EMA）",
        "icon": "ema_icon.png",
        "url": "https://www.ema.europa.eu/en/committees"
      },
      {
        "id": "common-cat-10-13-欧洲药典委",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "欧洲药典委",
        "description": "EDQM主页",
        "icon": "edqm_icon.png",
        "url": "https://www.edqm.eu/en/home"
      },
      {
        "id": "common-cat-10-14-澳大利亚药品管理局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "澳大利亚药品管理局",
        "description": "TGA主页",
        "icon": "tga_icon.png",
        "url": "https://www.tga.gov.au/"
      },
      {
        "id": "common-cat-10-15-药品查询（TGA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "药品查询（TGA）",
        "icon": "tga_icon.png",
        "url": "https://compliance.health.gov.au/artg/"
      },
      {
        "id": "common-cat-10-16-生物制品的监管和指南（TGA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "生物制品的监管和指南（TGA）",
        "icon": "tga_icon.png",
        "url": "https://www.tga.gov.au/products/biologicals-blood-and-tissues-and-advanced-therapies/biologicals"
      },
      {
        "id": "common-cat-10-17-指南和其他资源查询（TGA）",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "指南和其他资源查询（TGA）",
        "icon": "tga_icon.png",
        "url": "https://www.tga.gov.au/resources"
      },
      {
        "id": "common-cat-10-18-日本药监局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "日本药监局",
        "description": "PMDA主页",
        "icon": "pmda_icon.png",
        "url": "https://www.pmda.go.jp/"
      },
      {
        "id": "common-cat-10-19-韩国药监局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "韩国药监局",
        "description": "MFDS主页",
        "icon": "mfds_icon.png",
        "url": "https://www.mfds.go.kr/index.do"
      },
      {
        "id": "common-cat-10-20-印度药监局",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "印度药监局",
        "description": "MoHFW主页",
        "icon": "mohfw_icon.png",
        "url": "https://main.mohfw.gov.in/"
      },
      {
        "id": "common-cat-10-21-国际人用药品注册技术协调会",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "国际人用药品注册技术协调会",
        "description": "ICH主页，ICH指南查询",
        "icon": "ich_icon.png",
        "url": "https://www.ich.org/index.html"
      },
      {
        "id": "common-cat-10-22-监管活动医学词典",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "监管活动医学词典",
        "description": "MedDRA主页，在ICH的主办下编制的医学标准术语集",
        "icon": "Med_icon.png",
        "url": "https://www.meddra.org/"
      },
      {
        "id": "common-cat-10-23-国际制药工程学会",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "国际制药工程学会",
        "description": "ISPE主页，全球非营利组织，制药行业规范",
        "icon": "ISPE_icon.png",
        "url": "https://ispe.org/about"
      },
      {
        "id": "common-cat-10-24-国际药品认证合作组织",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "国际药品认证合作组织",
        "description": "PIC/S主页，各成员国互认的GMP规范",
        "icon": "pics_icon.png",
        "url": "https://picscheme.org/en/picscheme"
      },
      {
        "id": "common-cat-10-25-美国注射剂协会",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "美国注射剂协会",
        "description": "PDA主页，美国非营利组织，无菌工艺行业规范等资源",
        "icon": "pda_icon.png",
        "url": "https://www.pda.org/home"
      },
      {
        "id": "common-cat-10-26-Clinical-Trials",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "Clinical Trials",
        "description": "全球临床试验信息查询",
        "icon": "NIH_icon.png",
        "url": "https://www.clinicaltrials.gov/"
      },
      {
        "id": "common-cat-10-27-欧盟GMP指南",
        "tab": "common",
        "groupId": "common-cat-10",
        "groupName": "国外药监机构或协会",
        "name": "欧盟GMP指南",
        "description": "EudraLex - Volume 4",
        "icon": "ec_icon.png",
        "url": "https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en"
      }
    ]
  },
  {
    "id": "common-cat-11",
    "tab": "common",
    "name": "药典查询",
    "icon": "el-icon-notebook-2",
    "entries": [
      {
        "id": "common-cat-11-0-蒲标网",
        "tab": "common",
        "groupId": "common-cat-11",
        "groupName": "药典查询",
        "name": "蒲标网",
        "description": "中国药典查询",
        "icon": "ouryao_icon.png",
        "url": "https://db.ouryao.com/"
      },
      {
        "id": "common-cat-11-1-美国药典委（USP）",
        "tab": "common",
        "groupId": "common-cat-11",
        "groupName": "药典查询",
        "name": "美国药典委（USP）",
        "description": "美国药典，QC有买账号",
        "icon": "usp_icon.png",
        "url": "https://www.usp.org/"
      },
      {
        "id": "common-cat-11-2-各国药典",
        "tab": "common",
        "groupId": "common-cat-11",
        "groupName": "药典查询",
        "name": "各国药典",
        "description": "非最新，但很全，且免费",
        "icon": "drugfuture_icon.png",
        "url": "https://www.drugfuture.com/standard/"
      }
    ]
  },
  {
    "id": "common-cat-12",
    "tab": "common",
    "name": "财经资讯",
    "icon": "el-icon-s-finance",
    "entries": [
      {
        "id": "common-cat-12-0-巨潮资讯",
        "tab": "common",
        "groupId": "common-cat-12",
        "groupName": "财经资讯",
        "name": "巨潮资讯",
        "description": "巨潮资讯网是中国证监会指定的上市公司信息披露网站，平台提供上市公司公告、公司资讯、公司互动、股东大会网络投票等",
        "icon": "cninf.png",
        "url": "http://www.cninfo.com.cn/new/index"
      }
    ]
  },
  {
    "id": "ai-RAG",
    "tab": "ai",
    "name": "知识库",
    "icon": "el-icon-collection",
    "entries": [
      {
        "id": "ai-RAG-0-SOP问询助手",
        "tab": "ai",
        "groupId": "ai-RAG",
        "groupName": "知识库",
        "name": "SOP问询助手",
        "description": "通过公司知识库支持制度、流程、SOP问答。",
        "icon": "sop-qa-assistant.png",
        "color": "#00a6a6",
        "systemKey": "RAGFlowSOP"
      }
    ]
  },
  {
    "id": "ai-Other",
    "tab": "ai",
    "name": "办公工具",
    "icon": "el-icon-s-tools",
    "entries": [
      {
        "id": "ai-Other-0-Word文档翻译",
        "tab": "ai",
        "groupId": "ai-Other",
        "groupName": "办公工具",
        "name": "Word文档翻译",
        "description": "基于DeepSeekV4模型，支持Word文档中英互译，格式原样保留。",
        "icon": "el-icon-chat-line-square",
        "color": "#4f46e5",
        "url": "http://10.56.0.210:8000"
      },
      {
        "id": "ai-Other-1-Word文档审核",
        "tab": "ai",
        "groupId": "ai-Other",
        "groupName": "办公工具",
        "name": "Word文档审核",
        "description": "基于DeepSeekV4模型，上传Word文档后执行Word审核，并生成批注版Word文档。",
        "icon": "el-icon-s-check",
        "color": "#f97316",
        "systemKey": "WordReview"
      }
    ]
  },
  {
    "id": "ai-Intranet",
    "tab": "ai",
    "name": "AI平台",
    "icon": "el-icon-monitor",
    "entries": [
      {
        "id": "ai-Intranet-0-小智",
        "tab": "ai",
        "groupId": "ai-Intranet",
        "groupName": "AI平台",
        "name": "小智",
        "description": "小智AI - 要么随波逐流，要么驾驭小智AI，即刻登录，掌控全局。",
        "icon": "992a2dfcd3b932d05562580ad636b4c5.jpeg",
        "url": "http://innovateX-AI.intbio.com:2026/workspace"
      }
    ]
  }
];

export function filterLaunchpadGroups(
  groups: LaunchpadGroup[],
  query: string,
): LaunchpadGroup[] {
  const keyword = query.trim().toLowerCase();
  if (!keyword) return groups;

  return groups
    .map((group) => ({
      ...group,
      entries: group.entries.filter((entry) =>
        [entry.name, entry.description, entry.groupName, entry.systemKey]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(keyword)),
      ),
    }))
    .filter((group) => group.entries.length > 0);
}

export function resolveLaunchpadDestination(
  entry: LaunchpadEntry,
): LaunchpadDestination {
  if (entry.url) {
    return { kind: "url", href: entry.url };
  }

  if (entry.systemKey) {
    const href = buildLegacySystemUrl(entry.systemKey);
    if (href) {
      return { kind: "url", href };
    }
  }

  return {
    kind: "unavailable",
    reason: entry.unavailableReason || "待接入",
  };
}
