"""Concrete EngineAdapter implementations, one package per engine.

Design spec Section 4, V1 adapters table:

    VideoGeneratorAdapter   — launch(): real, in-process import of
                              run_video_generation(). monitor()/collect_results(): real.
    ImageGeneratorAdapter   — launch(): stubbed, no headless entry point exists yet.
                              monitor()/collect_results(): real, via job_manifest.json /
                              approved_media_handoff.json.
    MockupGeneratorAdapter  — launch(): stubbed, no headless entry point exists yet.
                              monitor()/collect_results(): real, via manifest.json
                              (defensive — known schema drift, see the handbook).
"""
