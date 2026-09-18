#!/usr/bin/env python3
"""
Kokoro Voice Studio CLI
Ultra-fast Text-to-Speech generation powered by Kokoro-82M.
"""

import sys
import os
from pathlib import Path
import argparse

# Enable Apple Silicon MPS fallback
if sys.platform == "darwin":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from app.config import VOICES, LANGUAGES, SAMPLE_RATE, get_device
from app.kokoro_engine import engine
from app.edge_engine import edge_engine

console = Console()

def list_voices_cmd(lang_filter=None):
    table = Table(title="Available Kokoro Voices (82M)", header_style="bold magenta")
    table.add_column("Voice ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Lang", style="green")
    table.add_column("Gender", style="yellow")
    table.add_column("Tags", style="blue")

    for v in VOICES:
        if lang_filter and v["lang"] != lang_filter:
            continue
        lang_info = LANGUAGES.get(v["lang"], {})
        flag = lang_info.get("flag", "")
        tags = ", ".join(v.get("tags", []))
        table.add_row(v["id"], v["name"], f"{flag} {v['lang']}", v["gender"], tags)

    console.print(table)

def list_languages_cmd():
    table = Table(title="Supported Kokoro Languages", header_style="bold cyan")
    table.add_column("Code", style="bold green")
    table.add_column("Name", style="white")
    table.add_column("Default Voice", style="magenta")
    table.add_column("Sample Text", style="dim")

    for code, info in LANGUAGES.items():
        table.add_row(
            f"{info['flag']} {code}",
            info["name"],
            info["default_voice"],
            info["sample_text"][:50] + "..."
        )

    console.print(table)

def main():
    parser = argparse.ArgumentParser(
        description="Kokoro-82M High-Fidelity Text-to-Speech CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py "Hello from Kokoro Voice Studio!" -o hello.wav
  python cli.py --file script.txt -v af_heart --speed 1.1 -o output.wav
  python cli.py --list-voices
  python cli.py --list-languages
  python cli.py "Bonjour le monde!" --lang f -v ff_siwis -o french.wav
  python cli.py "Blended voice example" -v af_heart --secondary-voice af_bella --blend 0.4 -o blend.wav
        """
    )

    parser.add_argument("text", nargs="?", help="Text string to synthesize into speech")
    parser.add_argument("-f", "--file", type=str, help="Path to input text file")
    parser.add_argument("-o", "--output", type=str, default="output.wav", help="Output WAV filepath (default: output.wav)")
    parser.add_argument("-v", "--voice", type=str, default="af_heart", help="Primary voice identifier (default: af_heart)")
    parser.add_argument("--secondary-voice", type=str, default=None, help="Optional secondary voice to blend with")
    parser.add_argument("--blend", type=float, default=0.0, help="Blend ratio for secondary voice (0.0 to 1.0, e.g. 0.3 for 30%)")
    parser.add_argument("-s", "--speed", type=float, default=1.0, help="Speech speed multiplier, e.g. 0.7x for slower, 1.2x for faster (default: 1.0)")
    parser.add_argument("-g", "--gap", type=float, default=0.25, help="Pause/gap duration between sentences/segments in seconds (default: 0.25)")
    parser.add_argument("-l", "--lang", type=str, default="a", help="Language code (a, b, e, f, h, i, j, p, z)")
    parser.add_argument("-e", "--engine", type=str, default="auto", choices=["auto", "edge", "kokoro"], help="TTS engine: 'edge' (Azure Neural), 'kokoro', or 'auto'")
    parser.add_argument("-p", "--split", type=str, default="newline", choices=["newline", "sentence", "none"], help="Text chunking strategy")
    parser.add_argument("--list-voices", action="store_true", help="List all available Kokoro voices")
    parser.add_argument("--list-languages", action="store_true", help="List all supported languages")

    args = parser.parse_args()

    if args.list_voices:
        list_voices_cmd(args.lang if args.lang != "a" else None)
        return

    if args.list_languages:
        list_languages_cmd()
        return

    # Determine input text
    input_text = ""
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[bold red]Error:[/bold red] File not found: {args.file}")
            sys.exit(1)
        input_text = file_path.read_text(encoding="utf-8")
    elif args.text:
        input_text = args.text
    else:
        # Prompt user interactively
        input_text = console.input("[bold cyan]Enter text to synthesize:[/bold cyan] ")

    input_text = input_text.strip()
    if not input_text:
        console.print("[bold red]Error:[/bold red] No text provided to synthesize.")
        sys.exit(1)

    device = get_device()
    console.print(Panel.fit(
        f"[bold green]Kokoro-82M Voice Studio[/bold green]\n"
        f"Device: [cyan]{device.upper()}[/cyan] | Language: [yellow]{args.lang}[/yellow] | Voice: [magenta]{args.voice}[/magenta]"
        + (f" + [magenta]{args.secondary_voice}[/magenta] ({int(args.blend*100)}%)" if args.secondary_voice else "")
        + f" | Speed: [white]{args.speed}x[/white] | Gap: [yellow]{args.gap}s[/yellow]",
        title="✨ Synthesis Task"
    ))

    with console.status("[bold green]Synthesizing speech...[/bold green]", spinner="dots"):
        try:
            use_edge = (
                args.engine == "edge"
                or (args.engine == "auto" and (args.lang in ["h", "hi"] or "neural" in str(args.voice).lower()))
            )

            if use_edge:
                voice_to_use = args.voice
                if voice_to_use == "af_heart":
                    voice_to_use = "hi-IN-SwaraNeural" if args.lang in ["h", "hi"] else "en-IN-NeerjaExpressiveNeural"
                result = edge_engine.synthesize(
                    text=input_text,
                    voice=voice_to_use,
                    language="hi" if args.lang in ["h", "hi"] else "en",
                    speed=args.speed
                )
            else:
                result = engine.synthesize(
                    text=input_text,
                    voice=args.voice,
                    secondary_voice=args.secondary_voice,
                    blend_weight=args.blend,
                    speed=args.speed,
                    lang_code=args.lang,
                    split_pattern=args.split,
                    gap_duration=args.gap
                )

            # Move or copy generated file to target output path
            generated_path = Path(result["filepath"])
            target_path = Path(args.output).resolve()
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if target_path != generated_path:
                import shutil
                shutil.copyfile(str(generated_path), str(target_path))

            console.print(f"[bold green]✓ Audio successfully generated![/bold green]")
            console.print(f"  • Destination: [cyan]{target_path}[/cyan]")
            console.print(f"  • Duration: [yellow]{result['duration_secs']}s[/yellow]")
            if result.get("segments_count") is not None:
                console.print(f"  • Segments: [white]{result['segments_count']}[/white]")
            if result.get("engine"):
                console.print(f"  • Engine: [green]{result['engine']}[/green]")

            # Print phonetic breakdown snippet
            if result.get("segments"):
                table = Table(title="Phonetic Breakdown", show_header=True, header_style="bold blue")
                table.add_column("#", style="dim", width=4)
                table.add_column("Graphemes (Text)", style="white")
                table.add_column("Phonemes (IPA/Tokens)", style="green")
                table.add_column("Dur", style="yellow", width=8)

                for seg in result["segments"][:5]:
                    table.add_row(
                        str(seg["index"] + 1),
                        seg["graphemes"][:40],
                        seg["phonemes"][:40],
                        f"{seg['duration_secs']}s"
                    )
                console.print(table)
                if len(result["segments"]) > 5:
                    console.print(f"[dim]... and {len(result['segments']) - 5} more segments[/dim]")

        except Exception as e:
            console.print(f"[bold red]Synthesis Error:[/bold red] {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
