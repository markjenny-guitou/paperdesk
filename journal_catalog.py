"""Editable, cross-discipline journal presets (not a ranking or exhaustive index).

One line per journal: canonical title | alternative names / common abbreviations.
Optional identifiers are kept separately in journal_identifiers.json.
"""
import hashlib
import json
from pathlib import Path

GROUPS = {
    '综合科学': '''
Nature|自然
Science|科学;Science (New York, N.Y.)
Cell|细胞
Nature Communications|自然通讯;Nat Commun
Science Advances|Sci Adv;科学进展
Proceedings of the National Academy of Sciences|PNAS;Proceedings of the National Academy of Sciences of the United States of America;美国国家科学院院刊
National Science Review|NSR;国家科学评论
Science Bulletin|科学通报英文版
Scientific Reports|Sci Rep;科学报告
PLOS ONE|PLoS One
Science China Life Sciences|中国科学生命科学英文版
Science China Technological Sciences|中国科学技术科学英文版
Royal Society Open Science
The Innovation
Research
Heliyon
''',
    '医学与公共卫生': '''
The New England Journal of Medicine|NEJM;New England Journal of Medicine;新英格兰医学杂志
The Lancet|Lancet;柳叶刀
JAMA|Journal of the American Medical Association;JAMA: The Journal of the American Medical Association
The BMJ|BMJ;British Medical Journal;BMJ (Clinical research ed.)
Nature Medicine|Nat Med;自然医学
Annals of Internal Medicine
JAMA Internal Medicine
JAMA Network Open
The Lancet Public Health
The Lancet Global Health
The Lancet Digital Health
The Lancet Healthy Longevity
The Lancet Regional Health - Western Pacific|The Lancet Regional Health – Western Pacific
The Lancet Regional Health - Europe|The Lancet Regional Health – Europe
BMJ Global Health
BMC Medicine
PLOS Medicine
eClinicalMedicine
eBioMedicine
American Journal of Public Health
International Journal of Epidemiology
Journal of Clinical Epidemiology
Annual Review of Public Health
Public Health
''',
    '临床专科与肿瘤': '''
CA: A Cancer Journal for Clinicians|CA Cancer J Clin;CA A Cancer Journal for Clinicians
Nature Cancer
Nature Reviews Cancer
The Lancet Oncology
Journal of Clinical Oncology|JCO
Cancer Cell
Cancer Discovery
Cancer Research
Clinical Cancer Research
Journal of the National Cancer Institute|JNCI
Annals of Oncology
Molecular Cancer
Circulation
Circulation Research
European Heart Journal
Journal of the American College of Cardiology|JACC
Nature Cardiovascular Research
The Lancet Diabetes & Endocrinology|The Lancet Diabetes and Endocrinology
Diabetes Care
Diabetologia
Gastroenterology
Gut
Hepatology
Journal of Hepatology
The Lancet Gastroenterology & Hepatology|The Lancet Gastroenterology and Hepatology
The Lancet Respiratory Medicine
American Journal of Respiratory and Critical Care Medicine
European Respiratory Journal
The Lancet Neurology
The Lancet Psychiatry
The Lancet Infectious Diseases
Clinical Infectious Diseases
The Lancet Rheumatology
Annals of the Rheumatic Diseases
Kidney International
Journal of the American Society of Nephrology|JASN
JAMA Surgery
Annals of Surgery
Radiology
Medical Image Analysis|Med Image Anal
''',
    '生命科学与生物技术': '''
Nature Biotechnology|Nat Biotechnol
Nature Methods|Nat Methods
Nature Genetics|Nat Genet
Nature Reviews Genetics
Nature Cell Biology
Nature Reviews Molecular Cell Biology
Nature Structural & Molecular Biology|Nature Structural and Molecular Biology
Nature Chemical Biology
Nature Microbiology
Nature Reviews Microbiology
Nature Immunology
Nature Reviews Immunology
Nature Metabolism
Nature Aging
Nature Ecology & Evolution|Nature Ecology and Evolution
Nature Plants
Nature Neuroscience
Nature Reviews Neuroscience
Cell Metabolism
Cell Host & Microbe|Cell Host and Microbe
Cell Stem Cell
Cell Systems
Cell Reports
Cell Reports Medicine
Molecular Cell
Developmental Cell
Immunity
Neuron
Current Biology
Trends in Biotechnology
Trends in Microbiology
Trends in Immunology
Trends in Genetics
Genome Biology
Genome Research
Nucleic Acids Research|NAR
Bioinformatics
Briefings in Bioinformatics
PLOS Biology
PLOS Genetics
PLOS Computational Biology
PLOS Pathogens
eLife
EMBO Journal|The EMBO Journal
EMBO Reports
Molecular Systems Biology
Journal of Cell Biology|The Journal of Cell Biology
Journal of Biological Chemistry|JBC
Microbiome
Gut Microbes
Microbial Biotechnology
Molecular Biology and Evolution
Cell Research
Protein & Cell|Protein and Cell
Signal Transduction and Targeted Therapy
Science Translational Medicine
Science Immunology
Science Signaling
''',
    '化学与化工': '''
Journal of the American Chemical Society|JACS;J Am Chem Soc
Angewandte Chemie International Edition|Angew Chem Int Ed
Chemical Reviews|Chem Rev
Chemical Society Reviews|Chem Soc Rev
Nature Chemistry|Nat Chem
Nature Catalysis
Nature Synthesis
Nature Reviews Chemistry
Chem
Chemistry of Materials
ACS Central Science
ACS Catalysis
ACS Sustainable Chemistry & Engineering|ACS Sustainable Chemistry and Engineering
ACS Energy Letters
ACS Applied Materials & Interfaces|ACS Applied Materials and Interfaces
ACS Nano
ACS Photonics
ACS Sensors
ACS Chemical Biology
ACS Medicinal Chemistry Letters
Analytical Chemistry
Analytica Chimica Acta
Chemical Science
Chemical Communications
Green Chemistry
Organic Letters
The Journal of Organic Chemistry|Journal of Organic Chemistry
Inorganic Chemistry
Dalton Transactions
Physical Chemistry Chemical Physics|PCCP
The Journal of Physical Chemistry Letters|Journal of Physical Chemistry Letters
The Journal of Physical Chemistry A|Journal of Physical Chemistry A
The Journal of Physical Chemistry B|Journal of Physical Chemistry B
The Journal of Physical Chemistry C|Journal of Physical Chemistry C
Chemical Engineering Journal|CEJ
AIChE Journal
Industrial & Engineering Chemistry Research|Industrial and Engineering Chemistry Research
Applied Catalysis B: Environment and Energy|Applied Catalysis B Environmental;Applied Catalysis B: Environmental
Journal of Catalysis
Separation and Purification Technology
Chemical Engineering Science
''',
    '材料与纳米': '''
Nature Materials|Nat Mater
Nature Nanotechnology|Nat Nanotechnol
Nature Reviews Materials
Advanced Materials|Adv Mater
Advanced Functional Materials|AFM;Adv Funct Mater
Advanced Science
Advanced Energy Materials|AEM
Advanced Optical Materials
Advanced Healthcare Materials
Small
Small Methods
Nano Letters
Nano Energy
Nano Today
Nano Research
Nano-Micro Letters
Materials Today
Materials Horizons
Nanoscale
Acta Materialia
Scripta Materialia
Progress in Materials Science
International Materials Reviews
Journal of Materials Chemistry A
Journal of Materials Chemistry B
Journal of Materials Chemistry C
Carbon
Composites Part A: Applied Science and Manufacturing
Composites Part B: Engineering
Composites Science and Technology
Ceramics International
Journal of the European Ceramic Society
Materials & Design|Materials and Design
Bioactive Materials
Biomaterials
Biomaterials Research
''',
    '能源与环境': '''
Nature Energy
Nature Sustainability
Nature Climate Change
Nature Water
Nature Reviews Earth & Environment|Nature Reviews Earth and Environment
Energy & Environmental Science|Energy and Environmental Science;EES
Joule
Energy Storage Materials
EnergyChem
Energy
Applied Energy
Renewable and Sustainable Energy Reviews|RSER
Renewable Energy
Energy Conversion and Management
Journal of Power Sources
Journal of Energy Chemistry
Solar Energy
Solar RRL
International Journal of Hydrogen Energy
Sustainable Energy & Fuels|Sustainable Energy and Fuels
Environmental Science & Technology|Environmental Science and Technology;ES&T
Environmental Science & Technology Letters|Environmental Science and Technology Letters
Water Research
Environmental Science: Nano
Environment International
Environmental Pollution
Science of the Total Environment|STOTEN
Journal of Hazardous Materials
Journal of Cleaner Production
Resources, Conservation and Recycling
Environmental Research Letters
Global Environmental Change
Environmental Research
Waste Management
''',
    '计算机与人工智能': '''
Nature Machine Intelligence
Nature Computational Science
Artificial Intelligence
Journal of Artificial Intelligence Research|JAIR
Journal of Machine Learning Research|JMLR
Machine Learning
Neural Networks
Neural Computation
Pattern Recognition
Pattern Recognition Letters
Information Sciences
Information Fusion
Expert Systems with Applications|ESWA
Knowledge-Based Systems
Applied Soft Computing
Data Mining and Knowledge Discovery
International Journal of Computer Vision|IJCV
Computational Linguistics
Transactions of the Association for Computational Linguistics|TACL
ACM Computing Surveys|CSUR
Communications of the ACM|CACM
Journal of the ACM|JACM
ACM Transactions on Graphics|ACM TOG
ACM Transactions on Information Systems|ACM TOIS
ACM Transactions on Database Systems|ACM TODS
ACM Transactions on Software Engineering and Methodology|ACM TOSEM
ACM Transactions on Computer-Human Interaction|ACM TOCHI
ACM Transactions on Intelligent Systems and Technology|ACM TIST
ACM Transactions on Knowledge Discovery from Data|ACM TKDD
IEEE Transactions on Pattern Analysis and Machine Intelligence|TPAMI;IEEE TPAMI
IEEE Transactions on Neural Networks and Learning Systems|TNNLS;IEEE TNNLS
IEEE Transactions on Image Processing|TIP;IEEE TIP
IEEE Transactions on Knowledge and Data Engineering|TKDE;IEEE TKDE
IEEE Transactions on Software Engineering|TSE;IEEE TSE
IEEE Transactions on Computers|IEEE TC
IEEE Transactions on Multimedia|TMM
IEEE Transactions on Visualization and Computer Graphics|TVCG
IEEE Transactions on Cybernetics|TCYB
IEEE Transactions on Fuzzy Systems|TFS
IEEE Transactions on Evolutionary Computation|TEVC
IEEE Transactions on Big Data
IEEE Transactions on Cloud Computing
IEEE Transactions on Affective Computing
IEEE Transactions on Information Forensics and Security|TIFS
IEEE Transactions on Dependable and Secure Computing|TDSC
IEEE/ACM Transactions on Networking|TON
IEEE Transactions on Mobile Computing|TMC
IEEE Transactions on Medical Imaging|TMI
IEEE Journal of Biomedical and Health Informatics|JBHI
IEEE Access
Computers & Security|Computers and Security
Computer Networks
Computer Vision and Image Understanding
Information Processing & Management|Information Processing and Management
''',
    '电子通信与自动控制': '''
Nature Electronics
Proceedings of the IEEE
IEEE Communications Surveys & Tutorials|IEEE Communications Surveys and Tutorials;COMST
IEEE Journal on Selected Areas in Communications|JSAC
IEEE Transactions on Communications|TCOM
IEEE Transactions on Wireless Communications|TWC
IEEE Transactions on Signal Processing|TSP
IEEE Signal Processing Magazine
IEEE Communications Magazine
IEEE Wireless Communications
IEEE Internet of Things Journal|IEEE IoT Journal;IOTJ
IEEE Transactions on Vehicular Technology|TVT
IEEE Transactions on Antennas and Propagation|TAP
IEEE Transactions on Microwave Theory and Techniques|TMTT
IEEE Journal of Solid-State Circuits|JSSC
IEEE Transactions on Electron Devices|TED
IEEE Electron Device Letters|EDL
IEEE Transactions on Circuits and Systems I: Regular Papers|IEEE Transactions on Circuits and Systems I Regular Papers
IEEE Transactions on Circuits and Systems II: Express Briefs|IEEE Transactions on Circuits and Systems II Express Briefs
IEEE Transactions on Industrial Electronics|TIE
IEEE Transactions on Industrial Informatics|TII
IEEE Transactions on Power Electronics|TPEL
IEEE Transactions on Power Systems|TPWRS
IEEE Transactions on Smart Grid|TSG
IEEE Transactions on Sustainable Energy|TSTE
IEEE Transactions on Energy Conversion
IEEE Transactions on Automatic Control|TAC
Automatica
IEEE Control Systems Letters
IEEE/CAA Journal of Automatica Sinica|JAS
Control Engineering Practice
Systems & Control Letters|Systems and Control Letters
''',
    '机械机器人与土木': '''
Science Robotics
IEEE Transactions on Robotics|TRO
The International Journal of Robotics Research|International Journal of Robotics Research;IJRR
IEEE Robotics and Automation Letters|RA-L;RAL
IEEE/ASME Transactions on Mechatronics
Robotics and Autonomous Systems
Robotics and Computer-Integrated Manufacturing
Mechanical Systems and Signal Processing|MSSP
International Journal of Mechanical Sciences
Journal of Sound and Vibration
International Journal of Machine Tools and Manufacture
International Journal of Heat and Mass Transfer
Applied Thermal Engineering
Journal of Manufacturing Processes
Additive Manufacturing
CIRP Annals
CIRP Journal of Manufacturing Science and Technology
International Journal of Production Research
International Journal of Production Economics
Engineering Structures
Structural Safety
Journal of Structural Engineering
Computer-Aided Civil and Infrastructure Engineering
Automation in Construction
Cement and Concrete Research
Cement and Concrete Composites
Construction and Building Materials
Building and Environment
Energy and Buildings
Transportation Research Part B: Methodological
Transportation Research Part C: Emerging Technologies
Transportation Research Part D: Transport and Environment
Transportation Research Part E: Logistics and Transportation Review
''',
    '物理光学与天文': '''
Nature Physics
Nature Photonics
Nature Astronomy
Nature Reviews Physics
Physical Review Letters|PRL
Reviews of Modern Physics|RMP
Physical Review X|PRX
Physical Review A
Physical Review B
Physical Review C
Physical Review D
Physical Review E
Physical Review Research
Physical Review Applied
Physical Review Materials
PRX Quantum
New Journal of Physics
Reports on Progress in Physics
Physics Reports
Applied Physics Letters|APL
Journal of Applied Physics
The Journal of Chemical Physics|Journal of Chemical Physics;JCP
Optica
Optics Express
Optics Letters
Light: Science & Applications|Light Science & Applications;Light Science and Applications
Laser & Photonics Reviews|Laser and Photonics Reviews
Photonics Research
Advanced Photonics
Nanophotonics
The Astrophysical Journal|Astrophysical Journal;ApJ
The Astrophysical Journal Letters|Astrophysical Journal Letters;ApJL
The Astrophysical Journal Supplement Series|Astrophysical Journal Supplement Series
Astronomy & Astrophysics|Astronomy and Astrophysics;A&A
Monthly Notices of the Royal Astronomical Society|MNRAS
The Astronomical Journal|Astronomical Journal
Annual Review of Astronomy and Astrophysics
''',
    '数学统计与运筹': '''
Annals of Mathematics
Inventiones mathematicae|Inventiones Mathematicae
Journal of the American Mathematical Society|JAMS
Acta Mathematica
Duke Mathematical Journal
Communications on Pure and Applied Mathematics
Communications in Mathematical Physics
Journal of Differential Equations
SIAM Review
SIAM Journal on Numerical Analysis
SIAM Journal on Scientific Computing
SIAM Journal on Optimization
SIAM Journal on Control and Optimization
Mathematical Programming
Operations Research
Mathematics of Operations Research
European Journal of Operational Research|EJOR
INFORMS Journal on Computing
Annals of Statistics|The Annals of Statistics
Annals of Probability|The Annals of Probability
Journal of the American Statistical Association|JASA
Journal of the Royal Statistical Society Series B: Statistical Methodology|Journal of the Royal Statistical Society Series B Statistical Methodology;JRSS B
Biometrika
Biometrics
Statistical Science
Journal of Computational and Graphical Statistics
Technometrics
''',
    '地球科学与地理': '''
Nature Geoscience
Earth and Planetary Science Letters|EPSL
Earth-Science Reviews
Reviews of Geophysics
Geophysical Research Letters|GRL
Journal of Geophysical Research: Atmospheres|Journal of Geophysical Research Atmospheres
Journal of Geophysical Research: Solid Earth|Journal of Geophysical Research Solid Earth
Journal of Geophysical Research: Oceans|Journal of Geophysical Research Oceans
Geology
Geological Society of America Bulletin|GSA Bulletin
Geochimica et Cosmochimica Acta
Geophysical Journal International
Journal of Climate
Climate Dynamics
Atmospheric Chemistry and Physics
Atmospheric Environment
Water Resources Research
Journal of Hydrology
Hydrology and Earth System Sciences
Remote Sensing of Environment|RSE
ISPRS Journal of Photogrammetry and Remote Sensing
IEEE Transactions on Geoscience and Remote Sensing|TGRS
International Journal of Geographical Information Science|IJGIS
Progress in Human Geography
Annals of the American Association of Geographers
Landscape and Urban Planning
Cities
Urban Studies
''',
    '生态农业与食品': '''
Ecology Letters
Ecology
Ecological Monographs
Journal of Ecology
Journal of Applied Ecology
Global Change Biology
Global Ecology and Biogeography
Ecography
Methods in Ecology and Evolution
Conservation Biology
Biological Conservation
Trends in Ecology & Evolution|Trends in Ecology and Evolution
Annual Review of Ecology, Evolution, and Systematics
The ISME Journal|ISME Journal
New Phytologist
The Plant Cell|Plant Cell
Plant Physiology
The Plant Journal|Plant Journal
Molecular Plant
Trends in Plant Science
Plant Biotechnology Journal
Agricultural and Forest Meteorology
Agriculture, Ecosystems & Environment|Agriculture Ecosystems & Environment
Field Crops Research
Soil Biology and Biochemistry
Geoderma
Food Chemistry
Food Hydrocolloids
Trends in Food Science & Technology|Trends in Food Science and Technology
Comprehensive Reviews in Food Science and Food Safety
Critical Reviews in Food Science and Nutrition
Journal of Agricultural and Food Chemistry
Food Research International
Aquaculture
Fish and Fisheries
''',
    '药学与生物医学工程': '''
Nature Reviews Drug Discovery
Drug Discovery Today
Trends in Pharmacological Sciences
Pharmacological Reviews
Advanced Drug Delivery Reviews
Journal of Controlled Release
International Journal of Pharmaceutics
European Journal of Pharmaceutics and Biopharmaceutics
Acta Pharmaceutica Sinica B
Journal of Medicinal Chemistry
European Journal of Medicinal Chemistry
Phytomedicine
Pharmacological Research
British Journal of Pharmacology
Clinical Pharmacology & Therapeutics|Clinical Pharmacology and Therapeutics
Nature Biomedical Engineering
Science Translational Medicine
Biomedical Materials
Acta Biomaterialia
Biofabrication
IEEE Transactions on Biomedical Engineering|TBME
Annals of Biomedical Engineering
Biosensors and Bioelectronics
''',
    '心理神经与行为': '''
Nature Human Behaviour
Psychological Bulletin
Psychological Review
Perspectives on Psychological Science
Psychological Science
Annual Review of Psychology
American Psychologist
Journal of Personality and Social Psychology|JPSP
Journal of Experimental Psychology: General
Journal of Applied Psychology
Clinical Psychology Review
Annual Review of Clinical Psychology
JAMA Psychiatry
Molecular Psychiatry
Biological Psychiatry
American Journal of Psychiatry|The American Journal of Psychiatry
Journal of Child Psychology and Psychiatry
Neuroscience & Biobehavioral Reviews|Neuroscience and Biobehavioral Reviews
NeuroImage
Brain
Brain, Behavior, and Immunity
Neuropsychopharmacology
Trends in Cognitive Sciences
Cognition
Developmental Science
''',
    '经济金融与管理': '''
American Economic Review|AER
The Quarterly Journal of Economics|Quarterly Journal of Economics;QJE
Journal of Political Economy|JPE
Econometrica
The Review of Economic Studies|Review of Economic Studies;REStud
Journal of Economic Literature
Journal of Economic Perspectives
Journal of Econometrics
Journal of Development Economics
Journal of Public Economics
Journal of International Economics
Journal of Environmental Economics and Management
The Journal of Finance|Journal of Finance;JF
Journal of Financial Economics|JFE
The Review of Financial Studies|Review of Financial Studies;RFS
Review of Finance
Journal of Financial and Quantitative Analysis
Journal of Banking & Finance|Journal of Banking and Finance
Management Science
Academy of Management Journal|AMJ
Academy of Management Review|AMR
Administrative Science Quarterly|ASQ
Strategic Management Journal|SMJ
Organization Science
Organization Studies
Journal of Management
Journal of Management Studies
Journal of International Business Studies|JIBS
Research Policy
Journal of Business Research
Journal of Marketing
Journal of Marketing Research
Journal of Consumer Research
Marketing Science
The Accounting Review|Accounting Review
Journal of Accounting Research
Journal of Accounting and Economics
''',
    '教育社会与人文': '''
Review of Educational Research
Educational Researcher
American Educational Research Journal
Learning and Instruction
Learning and Individual Differences
Educational Psychology Review
Computers & Education|Computers and Education
British Journal of Educational Technology
The Internet and Higher Education|Internet and Higher Education
Studies in Higher Education
Higher Education
Teaching and Teacher Education
American Sociological Review
American Journal of Sociology
Annual Review of Sociology
Social Forces
Demography
Social Science & Medicine|Social Science and Medicine
American Political Science Review
American Journal of Political Science
The Journal of Politics|Journal of Politics
International Organization
World Politics
International Security
Public Administration Review
Journal of Public Administration Research and Theory
New Media & Society|New Media and Society
Journal of Communication
Information, Communication & Society|Information Communication and Society
Scientometrics
Journal of Informetrics
Journal of the Association for Information Science and Technology|JASIST
Language
Applied Linguistics
The Modern Language Journal|Modern Language Journal
The American Historical Review|American Historical Review
Past & Present|Past and Present
Mind
The Philosophical Review|Philosophical Review
Ethics
''',
    '中文综合与理工': '''
中国科学: 数学|中国科学：数学;中国科学 数学
中国科学: 物理学 力学 天文学|中国科学：物理学 力学 天文学
中国科学: 化学|中国科学：化学
中国科学: 生命科学|中国科学：生命科学
中国科学: 地球科学|中国科学：地球科学
中国科学: 技术科学|中国科学：技术科学
中国科学: 信息科学|中国科学：信息科学
科学通报|Chinese Science Bulletin
物理学报|Acta Physica Sinica
化学学报|Acta Chimica Sinica
化学进展
高等学校化学学报
分析化学
催化学报|Chinese Journal of Catalysis
无机化学学报
有机化学
高分子学报
金属学报
材料导报
无机材料学报
复合材料学报
计算机学报|Chinese Journal of Computers
软件学报|Journal of Software
计算机研究与发展|Journal of Computer Research and Development
自动化学报|Acta Automatica Sinica
电子学报|Acta Electronica Sinica
通信学报|Journal on Communications
计算机辅助设计与图形学学报
控制与决策
控制理论与应用
中国电机工程学报
电力系统自动化
电工技术学报
机械工程学报
中国机械工程
仪器仪表学报
航空学报
宇航学报
土木工程学报
建筑结构学报
岩土工程学报
水利学报
环境科学
环境科学学报
中国环境科学
生态学报
应用生态学报
地理学报
地理研究
地球物理学报
地质学报
遥感学报
测绘学报
农业工程学报
中国农业科学
食品科学
食品与发酵工业
''',
    '中文医学与社会科学': '''
中华医学杂志|National Medical Journal of China
中华内科杂志
中华外科杂志
中华儿科杂志
中华妇产科杂志
中华肿瘤杂志
中华心血管病杂志
中华流行病学杂志
中华预防医学杂志
中华护理杂志
中国循证医学杂志
中国公共卫生
中国药学杂志
药学学报
中草药
中国中药杂志
中国社会科学
经济研究
管理世界
经济学(季刊)|经济学（季刊）
世界经济
金融研究
中国工业经济
数量经济技术经济研究
中国管理科学
管理科学学报
系统工程理论与实践
南开管理评论
会计研究
科研管理
科学学研究
中国软科学
社会学研究
人口研究
心理学报|Acta Psychologica Sinica
心理科学进展
教育研究
高等教育研究
电化教育研究
开放教育研究
中国图书馆学报
图书情报工作
情报学报
新闻与传播研究
法学研究
中国法学
政治学研究
历史研究
哲学研究
文学评论
外语教学与研究
''',
}


def load_catalog():
    identifiers_path = Path(__file__).with_name('journal_identifiers.json')
    identifiers = json.loads(identifiers_path.read_text(encoding='utf-8')) if identifiers_path.exists() else {}
    items, seen = [], set()
    for category, lines in GROUPS.items():
        for line in lines.strip().splitlines():
            title, _, alternatives = line.strip().partition('|')
            if not title or title.casefold() in seen:
                continue
            seen.add(title.casefold())
            items.append(dict(id=hashlib.sha256(title.encode()).hexdigest()[:16], title=title,
                              category=category, aliases=[x for x in alternatives.split(';') if x],
                              issns=identifiers.get(title, [])))
    return items


JOURNALS = load_catalog()
