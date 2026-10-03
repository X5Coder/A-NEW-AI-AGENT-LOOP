import numpy as np  
from PIL import Image  
a=np.array(Image.open(r"storage\files\images\391dea084132.png").convert("L"))  
m=a<128  
print("colsums", [int(m[:,i].sum()) for i in range(8,22)])  
print("rowsums", [int(m[i].sum()) for i in range(8,22)])  
print("colsums_end", [int(m[:,i].sum()) for i in range(618,632)])  
print("rowsums_end", [int(m[i].sum()) for i in range(178,192)])  
