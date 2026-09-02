export interface LaunchpadEntry {
  id: string;
  name: string;
  description: string;
  icon: string;
  url: string;
}

export interface LaunchpadGroup {
  id: string;
  name: string;
  entries: LaunchpadEntry[];
}

type SiteRow = readonly [
  category: string,
  name: string,
  url: string,
  icon: string,
  description: string,
];

const siteRows: readonly SiteRow[] = [
  ["内网登录","QMS质量管理系统","http://dms.intbio.com/portal/index.html#/login","QMS_icon.png","DMS&TMS系统"],
  ["内网登录","vDrive(内部)","http://vdr-internal.intbio.com/","内部网盘.png","DataRoom_内部员工数据分享、共同编辑以及传输平台"],
  ["内网登录","OA","http://oa.intbio.com:8090/login.jsp","OA_icon.png","OA流程管理系统"],
  ["内网登录","ERP","http://erp.intbio.com:8080/","erp_icon.png","请通过用友客户端登录"],
  ["内网登录","ai-platform","http://ai.intbio.com:18001/auth/login","992a2dfcd3b932d05562580ad636b4c5.jpeg","小智AI - 要么随波逐流，要么驾驭小智AI，即刻登录，掌控全局。"],
  ["内网登录","公司邮箱","https://qiye.aliyun.com/","emial.png","企业邮箱"],
  ["内网登录","GMP域账号自助管理","http://pwd.intbio.com/RDWeb/Pages/zh-CN/password.aspx","pwd.jpg","GMP域账号密码重置"],
  ["内网登录","合理化建议","http://vdr-internal.intbio.com/index.html#doc/enterprise/2121","d9576b3de14945df873fb75acd8f8342.jpg","公司合理化建议"],
  ["内网登录","员工满意度调研","https://www.wjx.cn/vm/YO9HJ5f.aspx#","满意度调研.jpg","员工满意度调研"],
  ["内网登录","vDrive(外部)","http://vdr.intellectivebio.com:6777/","我的网盘.png","DataRoom_PM、投融资、BD、QA等对外数据分享、共同编辑以及传输平台"],
  ["内网登录","公司官网","https://www.intellectivebio.com","innovateX.png","企业官方网站"],
  ["内网登录","VR","http://vr-internal.intbio.com:8090/","vr_icon.png","常熟总部VR"],
  ["内网登录","WMS系统","http://wms.intbio.com/#/login","WMS.png","仓库管理系统"],
  ["AI","Gemini","https://gemini.google.com/app","gemini-color.png","最新Gemini3"],
  ["AI","DeepSeek","https://chat.deepseek.com/sign_in","deepseek.jpg","深度求索，探索未至之境"],
  ["AI","Kimi","https://kimi.moonshot.cn/chat/cogtsnkudu60vho4b50g","kimi_icon.png","月之暗面"],
  ["AI","豆包","https://www.doubao.com/chat/","fill_w720_h480_g0_mark_1708362360-doubao_logo_new.jpg","字节跳动"],
  ["AI","智谱清言","https://chatglm.cn/main/alltoolsdetail","qinghua.jpg","智谱AI"],
  ["AI","通义千问","https://tongyi.aliyun.com/qianwen/","tongyi_icon.png","阿里大模型"],
  ["AI","文心一言","https://yiyan.baidu.com/","yiyan_icon.png","百度大模型"],
  ["AI","POE","https://poe.com/login","poe_icon.png","注册免费用各种顶级大模型"],
  ["AI","秘塔AI","https://metaso.cn/","mita_icon.png","写论文，了解专业知识的神器"],
  ["翻译","沉浸式翻译","https://app.immersivetranslate.com/","下载.png","保持排版"],
  ["翻译","deepL","https://www.deepl.com/translator","deepL_icon.png","号称最准确的翻译软件"],
  ["翻译","谷歌翻译","https://translate.google.com/","google_icon.png","老牌翻译"],
  ["绘图","Excalidraw","https://excalidraw.com/","exca_icon.png","强大免费，可协作的在线手绘风格工具"],
  ["绘图","Biorender","https://app.biorender.com/user/signup","bioRENDER_icon.png","强大，卡通风格研绘图工具"],
  ["绘图","Figdraw","https://www.figdraw.com/static/index.html#/","FiG_icon.png","国产biorender替代"],
  ["文献检索","谷歌学术","https://scholar.google.com/","scholar_icon.png","科研人必备，简单好用"],
  ["文献检索","Pubmed","https://pubmed.ncbi.nlm.nih.gov/","NIH_icon.png","大杂烩"],
  ["文献检索","易文献","http://lib.sgst.cn/","sgst_icon.png","官方机构，注册后免费下载文献"],
  ["文献检索","SCI-hub","https://www.sci-hub.st/","Sci-hub_icon.png","强大的文献下载网站，网址经常变"],
  ["文献检索","Library Genesis","https://libgen.re/scimag/","libgene_icon.png","强大的图书下载网站，网址经常变"],
  ["文献检索","bookfi","https://z-library.cc/","zlib_icon.png","强大的图书下载网站，网址经常变"],
  ["文献期刊","Mabs","http://www.tandfonline.com/action/showAxaArticles?journalCode=kmab20","a.jpg","抗体人必读的顶级期刊"],
  ["文献期刊","Antibodies","https://www.mdpi.com/journal/antibodies","MDPI_icon.png","质量一般，胜在免费"],
  ["文献期刊","Biotechnology and Bioengineering","http://onlinelibrary.wiley.com/journal/10.1002/(ISSN)1097-0290","wiley_icon.png","工艺人必读的顶级期刊"],
  ["文献期刊","Biotechnology Progress","https://aiche.onlinelibrary.wiley.com/journal/15206033?journalRedirectCheck=true","AIChE_icon.png","工艺人必读的期刊"],
  ["文献期刊","Journal of Chromatography A","https://www.sciencedirect.com/journal/journal-of-chromatography-a","ScienceD_icon.png","分析人必读期刊"],
  ["文献期刊","Journal of Chromatography B","https://www.sciencedirect.com/journal/journal-of-chromatography-b","ScienceD_icon.png","分析人必读期刊"],
  ["文献期刊","European Journal of Pharmaceutics and Biopharmaceutics","https://www.sciencedirect.com/journal/european-journal-of-pharmaceutics-and-biopharmaceutics","ScienceD_icon.png","制剂人必读期刊"],
  ["专利检索","The Lens","https://www.lens.org/","lens_icon.png","强大，免费，专利搜索和下载"],
  ["专利检索","USPTO","https://patentcenter.uspto.gov/search?query=","uspto_icon.png","美国专利搜索下载"],
  ["专利检索","Free patent online","https://www.freepatentsonline.com/","fpo_icon.png","免费专利搜索下载"],
  ["专利检索","专利之星","https://cprs.patentstar.com.cn/Search/Index","cprs_icon.png","中国专利，注册后免费下载"],
  ["专利检索","谷歌专利","https://patents.google.com/","patents_icon.png","强大，免费，专利搜索和下载"],
  ["药物蛋白数据库","FDA Approved Drug Products","https://www.accessdata.fda.gov/scripts/cder/daf/","FDA_icon.png","FDA批准药物"],
  ["药物蛋白数据库","PMDA Approved Drug Products","https://www.pmda.go.jp/english/review-services/reviews/approved-information/drugs/0003.html","pmda_icon.png","PMDA批准药物"],
  ["药物蛋白数据库","EMA Approved Drug Products","https://www.ema.europa.eu/en/medicines","EUMed_icon.png","EMA批准药物"],
  ["药物蛋白数据库","Drugbank","https://www.drugbank.com/","drugbank_icon.png","药物信息查询大全"],
  ["药物蛋白数据库","The Structural Antibody Database","https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab","SAbDab_icon.png","抗体序列数据库"],
  ["药物蛋白数据库","IMGT","https://www.imgt.org/","IMGT_icon.png","强大的免疫组库"],
  ["药物蛋白数据库","RCBS","https://www.rcsb.org/","PDB_icon.png","蛋白结构数据库"],
  ["药物蛋白数据库","Uniprot","https://www.uniprot.org/","uniprot_icon.png","蛋白信息、结构大全"],
  ["药物蛋白数据库","NCBI","https://www.ncbi.nlm.nih.gov/gene/","NIH_icon.png","老牌经典"],
  ["药物蛋白数据库","insight数据库","https://db.dxy.cn/v5/home","insight_icon.png","公司有买账号"],
  ["药物蛋白数据库","中国药品价格查询","https://www.pkufh.com/Interactions/HisInquiry/MedicalPrice?medicalPriceTypeId=1","pkufh_icon.png","医保价格"],
  ["预测工具","SMS","http://www.bio-soft.net/sms/","sms_icon.png","序列处理工具包"],
  ["预测工具","SignalP – 6.0","https://services.healthtech.dtu.dk/services/SignalP-6.0/","dtu_icon-1.png","信号肽预测"],
  ["预测工具","ProtParam","https://web.expasy.org/protparam/","sib_icon.png","消光系数和等电点预测"],
  ["预测工具","Isoelectric Point Calculator 2.0","https://ipc2.mimuw.edu.pl/","ipc_icon.png","等电点预测"],
  ["预测工具","ExPAsy","https://www.expasy.org/","sib_icon.png","各种预测工具大全"],
  ["预测工具","Unimod","https://www.unimod.org/login.php?message=expired","unimod_icon.png","质谱修饰分子量"],
  ["预测工具","YinOyang","https://services.healthtech.dtu.dk/services/YinOYang-1.2/","dtu_icon-1.png","O糖修饰预测"],
  ["预测工具","abysis","http://www.abysis.org/abysis/","abY_icon.png","CDR预测和人源化"],
  ["预测工具","TAP","https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabpred/tap","SAbDab_icon.png","成药性预测"],
  ["预测工具","protein-sol","https://protein-sol.manchester.ac.uk/","protein_icon.png","成药性预测"],
  ["中国药监机构或协会","国家市场监督管理总局","https://www.samr.gov.cn/jg/index.html","yaojian_icon.png","国务院直属机构，管理NMPA"],
  ["中国药监机构或协会","国家药品监督管理局","https://www.nmpa.gov.cn/index.html","yaojian_icon.png","NMPA主页，含法规文件、药监动态等"],
  ["中国药监机构或协会","药品数据查询（NMPA）","https://www.nmpa.gov.cn/datasearch/home-index.html#category=yp","yaojian_icon.png",""],
  ["中国药监机构或协会","药品审评中心","https://www.cde.org.cn/","NMPA_icon.png","CDE主页，含政策法规、信息公开、主题专栏等"],
  ["中国药监机构或协会","受理品种查询（CDE）","https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d","NMPA_icon.png",""],
  ["中国药监机构或协会","共性问题回答（CDE）","https://www.cde.org.cn/main/xxgk/listpage/07edef25f1e7354bfd8490baa0ce056b","NMPA_icon.png",""],
  ["中国药监机构或协会","审评任务公示（CDE）","https://www.cde.org.cn/main/xxgk/listpage/369ac7cfeb67c6000c33f85e6f374044","NMPA_icon.png",""],
  ["中国药监机构或协会","临床默示许可查询（CDE）","https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c","NMPA_icon.png",""],
  ["中国药监机构或协会","上市药品信息（CDE）","https://www.cde.org.cn/main/xxgk/listpage/b40868b5e21c038a6aa8b4319d21b07d","NMPA_icon.png",""],
  ["中国药监机构或协会","原辅包登记信息（CDE）","https://www.cde.org.cn/main/xxgk/listpage/ba7aed094c29ae31467c0a35463a716e","NMPA_icon.png",""],
  ["中国药监机构或协会","指导原则专栏（CDE）","https://www.cde.org.cn/zdyz/index","NMPA_icon.png",""],
  ["中国药监机构或协会","ICH工作办公室专栏（CDE）","https://www.cde.org.cn/ichWeb/index.jsp","NMPA_icon.png",""],
  ["中国药监机构或协会","eCTD专栏（CDE）","https://www.cde.org.cn/ectd/index","NMPA_icon.png",""],
  ["中国药监机构或协会","中国食品药品检定研究院","https://www.nifdc.org.cn/nifdc/","nifdc_icon.png","NIFDC主页，负责药品注册检验等"],
  ["中国药监机构或协会","国家药典委员会","https://www.chp.org.cn/#/index","ChP_icon.png","CPC主页，负责药典编制、国家药品标准和药品通用名核准等"],
  ["中国药监机构或协会","食品药品审核查验中心","https://www.cfdi.org.cn/cfdi","cfdi_icon.png","CFDI主页，负责药品现场检查等"],
  ["中国药监机构或协会","药品审评检查长三角分中心","https://www.ydcdei.org.cn/","ydcdei_icon.png","CDE和CFDI在长三角的分中心"],
  ["中国药监机构或协会","江苏省药品监督管理局","http://da.jiangsu.gov.cn/","yaojian_icon.png","JSMPA主页"],
  ["中国药监机构或协会","\"药小问\"普法园地专栏（JSMPA）","https://da.jiangsu.gov.cn/col/col84698/index.html","cnppa_icon.png",""],
  ["中国药监机构或协会","已上市药品变更备案专栏（JSMPA）","https://da.jiangsu.gov.cn/col/col84704/index.html","yaojian_icon.png",""],
  ["中国药监机构或协会","咨询信件列表（JSMPA）","https://da.jiangsu.gov.cn/jact/front/mailpublist.do?sysid=107","yaojian_icon.png",""],
  ["中国药监机构或协会","中国医药包装协会","https://www.cnppa.org/","cnppa_icon.png","CNPPA主页，非营利性社会组织，医药包装领域行业标准等资源"],
  ["国外药监机构或协会","美国食品药品监督管理局","https://www.fda.gov/","FDA_icon.png","FDA主页"],
  ["国外药监机构或协会","指导原则查询（FDA）","https://www.fda.gov/regulatory-information/search-fda-guidance-documents","FDA_icon.png",""],
  ["国外药监机构或协会","生物制品指导原则等（FDA）","https://www.fda.gov/vaccines-blood-biologics/guidance-compliance-regulatory-information-biologics","FDA_icon.png",""],
  ["国外药监机构或协会","IND、BLA申报流程（FDA）","https://www.fda.gov/vaccines-blood-biologics/development-approval-process-cber","FDA_icon.png",""],
  ["国外药监机构或协会","FDA科学家对生物制品的科学研究（FDA）","https://www.fda.gov/vaccines-blood-biologics/science-research-biologics","FDA_icon.png",""],
  ["国外药监机构或协会","已获批药品信息库（FDA）","https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm","FDA_icon.png",""],
  ["国外药监机构或协会","紫皮书：FDA批准的生物制品数据库（FDA）","https://purplebooksearch.fda.gov/downloads","FDA_icon.png",""],
  ["国外药监机构或协会","已获批生物类似药信息库（FDA）","https://www.fda.gov/drugs/biosimilars/biosimilar-product-information","FDA_icon.png",""],
  ["国外药监机构或协会","美国联邦法规","https://www.ecfr.gov/","ecfr_icon.png","CFR主页，FDA由美国国会及联邦政府授权"],
  ["国外药监机构或协会","欧洲药品管理局","https://www.ema.europa.eu/en/homepage","ema_icon.png","EMA主页"],
  ["国外药监机构或协会","药品查询（EMA）","https://www.ema.europa.eu/en/medicines","ema_icon.png",""],
  ["国外药监机构或协会","法规指南查询（EMA）","https://www.ema.europa.eu/en/human-regulatory-overview","ema_icon.png",""],
  ["国外药监机构或协会","科学委员会和工作小组（EMA）","https://www.ema.europa.eu/en/committees","ema_icon.png",""],
  ["国外药监机构或协会","欧洲药典委","https://www.edqm.eu/en/home","edqm_icon.png","EDQM主页"],
  ["国外药监机构或协会","澳大利亚药品管理局","https://www.tga.gov.au/","tga_icon.png","TGA主页"],
  ["国外药监机构或协会","药品查询（TGA）","https://compliance.health.gov.au/artg/","tga_icon.png",""],
  ["国外药监机构或协会","生物制品的监管和指南（TGA）","https://www.tga.gov.au/products/biologicals-blood-and-tissues-and-advanced-therapies/biologicals","tga_icon.png",""],
  ["国外药监机构或协会","指南和其他资源查询（TGA）","https://www.tga.gov.au/resources","tga_icon.png",""],
  ["国外药监机构或协会","日本药监局","https://www.pmda.go.jp/","pmda_icon.png","PMDA主页"],
  ["国外药监机构或协会","韩国药监局","https://www.mfds.go.kr/index.do","mfds_icon.png","MFDS主页"],
  ["国外药监机构或协会","印度药监局","https://main.mohfw.gov.in/","mohfw_icon.png","MoHFW主页"],
  ["国外药监机构或协会","国际人用药品注册技术协调会","https://www.ich.org/index.html","ich_icon.png","ICH主页，ICH指南查询"],
  ["国外药监机构或协会","监管活动医学词典","https://www.meddra.org/","Med_icon.png","MedDRA主页，在ICH的主办下编制的医学标准术语集"],
  ["国外药监机构或协会","国际制药工程学会","https://ispe.org/about","ISPE_icon.png","ISPE主页，全球非营利组织，制药行业规范"],
  ["国外药监机构或协会","国际药品认证合作组织","https://picscheme.org/en/picscheme","pics_icon.png","PIC/S主页，各成员国互认的GMP规范"],
  ["国外药监机构或协会","美国注射剂协会","https://www.pda.org/home","pda_icon.png","PDA主页，美国非营利组织，无菌工艺行业规范等资源"],
  ["国外药监机构或协会","Clinical Trials","https://www.clinicaltrials.gov/","NIH_icon.png","全球临床试验信息查询"],
  ["国外药监机构或协会","欧盟GMP指南","https://health.ec.europa.eu/medicinal-products/eudralex/eudralex-volume-4_en","ec_icon.png","EudraLex - Volume 4"],
  ["药典查询","蒲标网","https://db.ouryao.com/","ouryao_icon.png","中国药典查询"],
  ["药典查询","美国药典委（USP）","https://www.usp.org/","usp_icon.png","美国药典，QC有买账号"],
  ["药典查询","各国药典","https://www.drugfuture.com/standard/","drugfuture_icon.png","非最新，但很全，且免费"],
  ["财经资讯","巨潮资讯","http://www.cninfo.com.cn/new/index","cninf.png","巨潮资讯网是中国证监会指定的上市公司信息披露网站，平台提供上市公司公告、公司资讯、公司互动、股东大会网络投票等"],
];

export const launchpadGroups: LaunchpadGroup[] = [];
const groupsByName = new Map<string, LaunchpadGroup>();

for (const [category, name, url, icon, description] of siteRows) {
  let group = groupsByName.get(category);
  if (!group) {
    group = {
      id: `web-category-${launchpadGroups.length}`,
      name: category,
      entries: [],
    };
    groupsByName.set(category, group);
    launchpadGroups.push(group);
  }

  group.entries.push({
    id: `${category}:${name}`,
    name,
    description,
    icon,
    url,
  });
}

export function getLaunchpadIconUrl(icon: string): string {
  return `/launchpad-icons/${encodeURIComponent(icon)}`;
}

export function filterLaunchpadGroups(
  groups: LaunchpadGroup[],
  query: string,
): LaunchpadGroup[] {
  const keyword = query.trim().toLowerCase();
  if (!keyword) return groups;

  return groups.flatMap((group) => {
    if (group.name.toLowerCase().includes(keyword)) return [group];

    const entries = group.entries.filter((entry) =>
      [entry.name, entry.description].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    );
    return entries.length ? [{ ...group, entries }] : [];
  });
}
