public class TotalArrayElements {

    public static int sumWithForLoop(int[] arr) {
        int total = 0;
        for (int i = 0; i < arr.length; i++) {
            total += arr[i];
        }
        return total;
    }

    public static int sumWithForEach(int[] arr) {
        int total = 0;
        for (int num : arr) {
            total += num;
        }
        return total;
    }

    public static int safeSum(int[] arr) {
        if (arr == null || arr.length == 0) {
            return 0;
        }
        int total = 0;
        for (int num : arr) {
            total += num;
        }
        return total;
    }

    public static void main(String[] args) {
        int[] numbers = {1, 2, 3, 4, 5, 6};

        System.out.println("Array: {1, 2, 3, 4, 5, 6}");
        System.out.println("sumWithForLoop:  " + sumWithForLoop(numbers));
        System.out.println("sumWithForEach:  " + sumWithForEach(numbers));

        int[] empty = {};
        int[] nullArr = null;

        System.out.println("\nsafeSum({1..6}):  " + safeSum(numbers));
        System.out.println("safeSum(empty):   " + safeSum(empty));
        System.out.println("safeSum(null):    " + safeSum(nullArr));
    }
}
