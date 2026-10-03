from PIL import Image  
im=Image.open(r"storage\files\images\391dea084132.png")  
c=im.crop((55,66,165,88))  
c=c.resize((c.width*8,c.height*8), Image.LANCZOS)  
c.save(r"storage\files\images\crop2.png")  
print(c.size)  
