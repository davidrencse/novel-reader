"""Print PyTorch + CUDA status. Run inside the venv by setup.ps1."""
import torch

cuda = torch.cuda.is_available()
name = torch.cuda.get_device_name(0) if cuda else "CPU only"
print(f"torch {torch.__version__} | CUDA available: {cuda} | {name}")
