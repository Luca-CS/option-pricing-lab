# Gradio app

Local interface to the full library, including live recalibration of the SLV leverage:

```bash
pip install gradio
python space/app.py
```

It loads only the calibrated parameters in `results/spx_calibration.json`. The YAML header that
Hugging Face would need is not included: hosting a Gradio Space now requires a paid plan, so the public
demo is the static page in `docs/`.
