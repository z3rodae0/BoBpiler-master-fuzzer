# fuzzer.py
# main 입니다.

from utils import*
from CodeGenerator import generate_c_code
from Analyzer import analyze_results
from running_system import*
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from queue import Queue
import logging
import uuid
import argparse
from itertools import repeat

logging.basicConfig(level=logging.WARNING)

import signal
import atexit

def signal_handler(sig, frame):
    print("Terminating all processes...")
    terminate_process_and_children(os.getpid())
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def cleanup():
    terminate_process_and_children(os.getpid())

atexit.register(cleanup)

# process_generator 함수: 생성기 별로 퍼징을 수행하는 함수
# argv: generator - 생성기 종류 (현재 csmith와 yarpgen)
# return: None 
def fuzz_with_generator(compilers, generator_config, temp_dirs, catch_dirs, partial_timeout=True):
    generator_name = generator_config["name"]
    machine_info = get_machine_info()
    round_number = 0  # 라운드 번호 초기화
    
    while True:
        print(f"\n{'#'*100}")
        print(f"[*] Fuzzing round: {round_number} for generator: {generator_name}")
        print(f"{'#'*100}\n")
        
        completed_tasks = 0
        skipped_tasks = 0
        
        try:
            for index in range(0, total_tasks):
                # 소스코드 생성
                print(f"****************************************generated by {generator_name}: {index} task started*********************************************")
                id = uuid.uuid1()    # 고유한 ID 셍성 - 어떤 컴퓨터에서 생성하든 생성된 코드를 구분하기 위함 
                dir_path, random_seed = generate_c_code(id, generator_config, temp_dirs)
                # 코드 생성이 되지 않은 경우 예외 처리 
                if dir_path is None:
                    logging.warning(f"Code generation failed for task {index} using {generator_name}, skipping.")
                    skipped_tasks += 1
                    continue

                
                with ProcessPoolExecutor() as executor:
                    futures = []
                    results = {}
                    # 컴파일 및 실행 (gcc, clang으로 -O0 ~ -O3 옵션 주어서 컴파일 하고 실행 결과 저장)
                    for compiler_info in compilers.values():
                        for opt_level in compiler_info['options']:
                            if 'runners' not in compiler_info['language'][generator_config['language']]: # 일반 컴파일러의 경우
                                futures.append(executor.submit(compile_and_run, dir_path, temp_dirs, generator_config, id, compiler_info, opt_level, random_seed))
                            else:
                                binary_name = os.path.join(temp_dirs, f'{id}', f"{compiler_info['file_name']}_{opt_level[1:]}")
                                compile_result = compile(binary_name, dir_path, generator_config, id, compiler_info, opt_level)
                                if compile_result['status']: # 컴파일이 성공한 경우에만 실행
                                    for runner_name, runner_command in compiler_info['language'][generator_config['language']]['runners'].items():
                                        futures.append(executor.submit(run_binary_for_wasm, runner_name, runner_command, compile_result, binary_name, generator_config, id, compiler_info, opt_level, random_seed))
                                else:
                                    result_dict = {
                                        'id': str(id),
                                        'random_Seed': str(random_seed),
                                        'compiler': compiler_info['name'],
                                        'optimization_level': opt_level,
                                        'generator': generator_name,
                                        'compile': {
                                            'status': None,
                                            'return_code': None,
                                            'error_type': None,
                                            'error_message': None
                                        },
                                        'run': {
                                            'status': None,
                                            'return_code': None,
                                            'error_type': None,
                                            'error_message': None,
                                            'result': None
                                        }
                                    }
                                    result_dict['compile'] = compile_result
                                    futures.append((binary_name, result_dict))  # 컴파일 실패 결과를 futures에 추가

                    for future in futures:
                        if isinstance(future, tuple):
                            key, result_dict = future
                        else:
                            result = future.result()
                            if result is not None:
                                key, result_dict = result
                        if key == "error": # 에러 처리
                            continue
                        results[key] = result_dict
                
                if len(results) > 0:  # results 딕셔너리가 비어 있지 않다면
                    analyze_results(compilers, dir_path, temp_dirs, catch_dirs, generator_config, id, random_seed, results, machine_info, partial_timeout)
                else:
                    # results 딕셔너리가 비어 있는 경우, 문제가 발생한 것으로 판단
                    skipped_tasks += 1
                    logging.critical(f"CRITICAL ERROR: This is an exceptional case which means impossible and requires immediate attention.")
                
                # Temp 폴더 청소
                cleanup_temp(temp_dirs)
                # 진행률 업데이트 및 출력
                completed_tasks += 1
                progress = (completed_tasks / total_tasks) * 100
                print(f"Progress for {generator_name}: {progress:.2f}% completed. skipped count: {skipped_tasks}")
                print(f"****************************************generated by {generator_name}: {index} task finished*********************************************")
            round_number += 1  # 라운드 번호 증가   
        except Exception as e:
            logging.error(f"An unexpected error occurred in process_generator for generator {generator_name} and task {index}: {e}")


# main 함수: 퍼징을 수행하는 총괄 코드
def main():
    try:
        parser = argparse.ArgumentParser(description="This is BoBpiler fuzzer")
        parser.add_argument("--no-timeout", action="store_false", dest="partial_timeout", help='Choose partial_timeout option')
        parser.add_argument('--endian', type=str, choices=['big', 'little'], default='little', help='Choose endian type')
        args = parser.parse_args()

        # 디렉토리 초기화
        setup_output_dirs()
        generators = list(generators_config.values())
        
        if platform.system() == 'Linux':
            if args.endian == 'big':
                compilers = linux_big_endian_compilers
            else:
                compilers = linux_little_endian_compilers
        elif platform.system() == 'Windows':
            compilers = window_compilers
        else:
            compilers = linux_little_endian_compilers
        
        temp_dirs_list = [TEMP_DIRS[generator["name"]] for generator in generators]
        catch_dirs_list = [CATCH_DIRS[generator["name"]] for generator in generators]
        with ProcessPoolExecutor() as executor:
            executor.map(fuzz_with_generator, repeat(compilers), generators, temp_dirs_list, catch_dirs_list, repeat(args.partial_timeout))
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received. Terminating all processes...")
        terminate_process_and_children(os.getpid())

if __name__ == "__main__":
    main()

