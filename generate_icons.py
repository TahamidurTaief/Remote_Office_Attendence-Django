import os
from PIL import Image, ImageDraw, ImageFont

sizes = [72, 96, 128, 144, 152, 192, 384, 512]

os.makedirs('static/icons', exist_ok=True)

for size in sizes:
    img = Image.new('RGB', (size, size), '#6366f1')
    draw = ImageDraw.Draw(img)
    
    # Draw "FT" text centered
    font_size = size // 3
    try:
        font = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            font_size)
    except:
        font = ImageFont.load_default()
    
    text = 'FT'
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (size - text_width) / 2
    y = (size - text_height) / 2
    draw.text((x, y), text, fill='white', font=font)
    
    img.save(f'static/icons/icon-{size}.png')
    print(f'Created icon-{size}.png')

print('All icons generated!')
