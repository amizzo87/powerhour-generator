import os
import sys
import shutil
import random
import subprocess
from glob import glob
from tempfile import TemporaryDirectory

def draw_progress_bar(progress, total, prefix='', length=50, fill='█', print_end="\r"):
    percent = "{0:.1f}".format(100 * (progress / float(total)))
    filled_length = int(length * progress // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    print(f'\r{prefix} |{bar}| {percent}% Complete', end=print_end)
    sys.stdout.flush()  # Flush the buffer

def run_command(command, log_file_path):
    with open(log_file_path, 'w') as log_file:
        result = subprocess.run(command, stderr=log_file, stdout=subprocess.DEVNULL)
    return result.returncode == 0

def get_video_duration(video_file):
    try:
        duration = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                            'format=duration', '-of',
                                            'default=noprint_wrappers=1:nokey=1', video_file],
                                           text=True).strip()
        return float(duration)
    except subprocess.CalledProcessError:
        print(f"Error getting duration for {video_file}")
        return None

def analyze_loudness(video_file, log_file_path):
    ffmpeg_command = [
        'ffmpeg', '-i', video_file, '-af', 'loudnorm=I=-23:LRA=7:print_format=json', '-f', 'null', '-'
    ]
    try:
        result = subprocess.check_output(ffmpeg_command, stderr=subprocess.STDOUT, text=True)
        loudness_info = {}
        for line in result.split('\n'):
            if ':' in line:
                key, value = line.strip().split(':', 1)
                loudness_info[key.strip()] = value.strip()
        return loudness_info
    except subprocess.CalledProcessError as e:
        print(f"Error analyzing loudness for {video_file}. See {log_file_path}")
        with open(log_file_path, 'w') as log_file:
            log_file.write(str(e.output))
        return None

def reencode_videos(video_file, start_time, duration, output_path, log_file_path, fade_duration, audio_loudness):
    fade_in_start = 0  # Start fade in at the beginning of the video
    fade_out_start = max(duration - fade_duration, 0)  # Start fade out fade_duration seconds before the end

    # Audio filters
    audio_filters = f"loudnorm=I=-23:LRA=7:TP=-1.5:measured_I={audio_loudness['input_i']}:measured_LRA={audio_loudness['input_lra']}:measured_TP={audio_loudness['input_tp']}:measured_thresh={audio_loudness['input_thresh']}:offset={audio_loudness['target_offset']}:linear=true:print_format=summary"

    ffmpeg_command = [
        'ffmpeg', '-y', '-ss', str(start_time), '-t', str(duration), '-i', video_file,
        '-vf', f"scale=1280:720, fade=t=in:st={fade_in_start}:d={fade_duration}, fade=t=out:st={fade_out_start}:d={fade_duration}",
        '-af', audio_filters,
        '-r', '30', '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', output_path
    ]
    return run_command(ffmpeg_command, log_file_path)

def main(video_folder, common_clip, fade_duration, output_file):
    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        print("ffmpeg or ffprobe is not installed. Please install them before running this script.")
        return

    if not os.path.isdir(video_folder) or not os.path.exists(common_clip):
        print("Specified video folder or common clip file does not exist.")
        return

    video_files = glob(os.path.join(video_folder, '*'))
    if not video_files:
        print("No video files found in the specified directory.")
        return

    with TemporaryDirectory() as temp_dir:
        ffmpeg_logs_dir = os.path.join(temp_dir, 'logs')
        os.makedirs(ffmpeg_logs_dir, exist_ok=True)

        max_videos = 60
        video_files = random.sample(video_files, min(max_videos, len(video_files)))

        print("Analyzing loudness and checking durations...")
        loudness_results = {}
        for i, video_file in enumerate(video_files):
            duration = get_video_duration(video_file)
            # Ensure video is at least 80 seconds to allow for a 60-second clip and 10-second buffer on both ends
            if duration and duration >= 80:
                loudness_info = analyze_loudness(video_file, os.path.join(ffmpeg_logs_dir, f'loudness_{i:04d}.log'))
                if loudness_info:
                    loudness_results[video_file] = (duration, loudness_info)
            draw_progress_bar(i + 1, len(video_files))
        print("\nFinished analyzing loudness and checking durations.")

        common_clip_temp = os.path.join(temp_dir, 'common_clip.mp4')
        if not reencode_videos(common_clip, 0, fade_duration, common_clip_temp, os.path.join(ffmpeg_logs_dir, 'common_clip.log'), fade_duration, {}):
            print(f"Failed to re-encode common clip: {common_clip}")
            return

        clip_list, failed = [], 0
        for i, (video_file, (duration, loudness_info)) in enumerate(loudness_results.items(), start=1):
            start_time = random.randint(10, int(duration) - 70)  # Adjusted start_time selection
            temp_clip_name = f'temp_clip_{i:04d}.mp4'
            temp_clip_path = os.path.join(temp_dir, temp_clip_name)

            if reencode_videos(video_file, start_time, 60, temp_clip_path, os.path.join(ffmpeg_logs_dir, f'video_{i:04d}.log'), fade_duration, loudness_info):
                clip_list.append(temp_clip_path)
            else:
                print(f"Failed to process video: {video_file}")
                failed += 1
            draw_progress_bar(i, len(video_files), prefix='Processing: ')

        print(f"\nProcessing complete. {failed} files failed to process.")

        concat_list_path = os.path.join(temp_dir, 'concat_list.txt')
        with open(concat_list_path, 'w') as concat_file:
            for i, clip_path in enumerate(clip_list):
                if i > 0:  # Add common clip before each video except the first one
                    concat_file.write(f"file '{common_clip_temp}'\n")
                concat_file.write(f"file '{clip_path}'\n")

        concat_command = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path, '-c', 'copy', output_file]
        if not run_command(concat_command, os.path.join(ffmpeg_logs_dir, 'concat.log')):
            print(f"Failed to concatenate videos. See {os.path.join(ffmpeg_logs_dir, 'concat.log')}")

if __name__ == '__main__':
    if len(sys.argv) != 5:
        print("Usage: python script_name.py /path/to/video/folder /path/to/common_clip.mp4 fade_duration_in_seconds output_file_name.mp4")
        sys.exit(1)

    video_folder_path = sys.argv[1]
    common_clip_path = sys.argv[2]
    fade_duration_seconds = float(sys.argv[3])
    output_file_name = sys.argv[4]

    main(video_folder_path, common_clip_path, fade_duration_seconds, output_file_name)
