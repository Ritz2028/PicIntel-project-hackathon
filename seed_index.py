import os
from app import index_local_image

SEED_DIR = 'seed_images'

def main():
    os.makedirs(SEED_DIR, exist_ok=True)
    for fn in os.listdir(SEED_DIR):
        path = os.path.join(SEED_DIR, fn)
        if not os.path.isfile(path): 
            continue
        try:
            index_local_image(path, fn, source_url='', title=fn, domain='local')
            print('Indexed:', fn)
        except Exception as e:
            print('Skip:', fn, e)

if __name__ == '__main__':
    main()
