import numpy as np  
from PIL import Image  
a=np.array(Image.open(r"storage\files\images\391dea084132.png").convert("L"))  
m=a<128  
inner=m[14:186,14:626]  
cols=inner.sum(axis=0)  
nz=np.nonzero(cols)[0]  
print("text col range", nz.min()+14, nz.max()+14)  
rows=inner.sum(axis=1)  
nzr=np.nonzero(rows)[0]  
print("text row range", nzr.min()+14, nzr.max()+14)  
print("colprofile", [int(x) for x in cols[nz.min():nz.min()+60]])  
